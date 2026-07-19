#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

static const NSInteger FlowLabPort = 8787;

@interface FlowLabAppDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSTask *backend;
@property(nonatomic, strong) NSTimer *startupTimer;
@property(nonatomic, strong) NSFileHandle *backendLog;
@property(nonatomic, strong) NSMutableSet<WKDownload *> *downloads;
@property(nonatomic) NSInteger attempts;
@end

@implementation FlowLabAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self createWindow];
    [self startBackend];
    [NSApp activateIgnoringOtherApps:YES];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self.startupTimer invalidate];
    [self.webView.configuration.userContentController removeScriptMessageHandlerForName:@"flowlabDesktop"];
    if (self.backend.running) [self.backend terminate];
    [self.backendLog closeFile];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (NSURL *)resourceURL:(NSString *)relative {
    return [[[NSBundle mainBundle] resourceURL] URLByAppendingPathComponent:relative];
}

- (NSURL *)supportURL {
    NSURL *base = [[[NSFileManager defaultManager] URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask] firstObject];
    NSURL *directory = [base URLByAppendingPathComponent:@"FlowLab" isDirectory:YES];
    [[NSFileManager defaultManager] createDirectoryAtURL:directory withIntermediateDirectories:YES attributes:nil error:nil];
    return directory;
}

- (NSString *)pythonExecutable {
    NSString *override = [NSProcessInfo processInfo].environment[@"FLOWLAB_PYTHON"];
    if (override.length > 0) return override;
    NSString *configured = [NSString stringWithContentsOfURL:[self resourceURL:@"python-path.txt"] encoding:NSUTF8StringEncoding error:nil];
    configured = [configured stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    return configured.length > 0 ? configured : @"/usr/bin/python3";
}

- (void)createWindow {
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = [WKWebsiteDataStore defaultDataStore];
    [configuration.userContentController addScriptMessageHandler:self name:@"flowlabDesktop"];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;
    self.webView.UIDelegate = self;
    self.downloads = [NSMutableSet set];
    self.window = [[NSWindow alloc]
        initWithContentRect:NSMakeRect(0, 0, 1512, 982)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered
        defer:NO];
    self.window.title = @"FlowLab";
    self.window.minSize = NSMakeSize(1180, 760);
    self.window.contentView = self.webView;
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [self showStatus:@"Starting the local FlowLab solver service…"];
}

- (void)webView:(WKWebView *)webView
    runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters
    initiatedByFrame:(WKFrameInfo *)frame
    completionHandler:(void (^)(NSArray<NSURL *> * _Nullable URLs))completionHandler {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        completionHandler(result == NSModalResponseOK ? panel.URLs : nil);
    }];
}

- (void)webView:(WKWebView *)webView
    decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction
    decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    decisionHandler(navigationAction.shouldPerformDownload ? WKNavigationActionPolicyDownload : WKNavigationActionPolicyAllow);
}

- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)navigationAction didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
    [self.downloads addObject:download];
}

- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)navigationResponse didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
    [self.downloads addObject:download];
}

- (void)download:(WKDownload *)download
    decideDestinationUsingResponse:(NSURLResponse *)response
    suggestedFilename:(NSString *)suggestedFilename
    completionHandler:(void (^)(NSURL * _Nullable destination))completionHandler {
    NSSavePanel *panel = [NSSavePanel savePanel];
    [self configureSavePanel:panel filename:(suggestedFilename.length > 0 ? suggestedFilename : @"FlowLab export")];
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        completionHandler(result == NSModalResponseOK ? panel.URL : nil);
    }];
    dispatch_async(dispatch_get_main_queue(), ^{ [panel validateVisibleColumns]; });
}

- (void)downloadDidFinish:(WKDownload *)download {
    [self.downloads removeObject:download];
}

- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)resumeData {
    [self.downloads removeObject:download];
    [self dispatchSaveResult:@"error" message:error.localizedDescription ?: @"Download failed."];
}

- (void)userContentController:(WKUserContentController *)userContentController didReceiveScriptMessage:(WKScriptMessage *)message {
    if (![message.name isEqualToString:@"flowlabDesktop"] || ![message.body isKindOfClass:[NSDictionary class]]) return;
    NSString *host = message.frameInfo.securityOrigin.host;
    if (![host isEqualToString:@"127.0.0.1"] && ![host isEqualToString:@"localhost"]) return;

    NSDictionary *payload = (NSDictionary *)message.body;
    if (![payload[@"type"] isEqualToString:@"save-files"] || ![payload[@"files"] isKindOfClass:[NSArray class]]) return;
    NSArray *files = (NSArray *)payload[@"files"];
    if (files.count == 0 || files.count > 24) {
        [self dispatchSaveResult:@"error" message:@"The export contained an invalid number of files."];
        return;
    }
    if (files.count == 1) {
        [self saveSingleFile:files.firstObject];
    } else {
        [self saveFilesToDirectory:files];
    }
}

- (BOOL)writeExportFile:(NSDictionary *)file toURL:(NSURL *)url error:(NSError **)error {
    NSString *text = [file[@"text"] isKindOfClass:[NSString class]] ? file[@"text"] : nil;
    if (text == nil) {
        if (error) *error = [NSError errorWithDomain:@"FlowLabDesktop" code:1 userInfo:@{NSLocalizedDescriptionKey: @"Export text was missing."}];
        return NO;
    }
    return [text writeToURL:url atomically:YES encoding:NSUTF8StringEncoding error:error];
}

- (void)saveSingleFile:(NSDictionary *)file {
    NSString *filename = [file[@"filename"] isKindOfClass:[NSString class]] ? [file[@"filename"] lastPathComponent] : @"FlowLab export";
    NSSavePanel *panel = [NSSavePanel savePanel];
    [self configureSavePanel:panel filename:(filename.length > 0 ? filename : @"FlowLab export")];
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSModalResponseOK) {
            [self dispatchSaveResult:@"cancelled" message:@"Export cancelled."];
            return;
        }
        NSError *error = nil;
        if ([self writeExportFile:file toURL:panel.URL error:&error]) {
            [self dispatchSaveResult:@"saved" message:[NSString stringWithFormat:@"Exported %@.", panel.URL.lastPathComponent]];
        } else {
            [self dispatchSaveResult:@"error" message:error.localizedDescription ?: @"Export failed."];
        }
    }];
    dispatch_async(dispatch_get_main_queue(), ^{ [panel validateVisibleColumns]; });
}

- (void)configureSavePanel:(NSSavePanel *)panel filename:(NSString *)filename {
    panel.canCreateDirectories = YES;
    panel.canSelectHiddenExtension = YES;
    panel.extensionHidden = NO;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    NSString *extension = filename.pathExtension;
    if (extension.length > 0) panel.allowedFileTypes = @[extension];
#pragma clang diagnostic pop
    panel.nameFieldStringValue = filename;
}

- (void)saveFilesToDirectory:(NSArray *)files {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = NO;
    panel.canChooseDirectories = YES;
    panel.allowsMultipleSelection = NO;
    panel.canCreateDirectories = YES;
    panel.prompt = @"Export";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSModalResponseOK) {
            [self dispatchSaveResult:@"cancelled" message:@"Export cancelled."];
            return;
        }
        NSURL *directory = panel.URL;
        for (NSDictionary *file in files) {
            NSString *filename = [file[@"filename"] isKindOfClass:[NSString class]] ? [file[@"filename"] lastPathComponent] : nil;
            if (filename.length == 0) continue;
            NSError *error = nil;
            if (![self writeExportFile:file toURL:[directory URLByAppendingPathComponent:filename] error:&error]) {
                [self dispatchSaveResult:@"error" message:error.localizedDescription ?: @"Export failed."];
                return;
            }
        }
        [self dispatchSaveResult:@"saved" message:[NSString stringWithFormat:@"Exported %lu files.", (unsigned long)files.count]];
    }];
}

- (void)dispatchSaveResult:(NSString *)status message:(NSString *)message {
    NSDictionary *detail = @{ @"status": status ?: @"error", @"message": message ?: @"Export failed." };
    NSData *data = [NSJSONSerialization dataWithJSONObject:detail options:0 error:nil];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    NSString *script = [NSString stringWithFormat:@"window.dispatchEvent(new CustomEvent('flowlab-desktop-save-result',{detail:%@}));", json];
    [self.webView evaluateJavaScript:script completionHandler:nil];
}

- (void)startBackend {
    NSURL *support = [self supportURL];
    NSURL *logURL = [support URLByAppendingPathComponent:@"flowlab-backend.log"];
    [[NSFileManager defaultManager] createFileAtPath:logURL.path contents:nil attributes:nil];
    self.backendLog = [NSFileHandle fileHandleForWritingAtPath:logURL.path];

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:[self pythonExecutable]];
    task.arguments = @[@"-m", @"uvicorn", @"server.app:app", @"--host", @"127.0.0.1", @"--port", [NSString stringWithFormat:@"%ld", (long)FlowLabPort]];
    task.currentDirectoryURL = support;
    NSMutableDictionary *environment = [[NSProcessInfo processInfo].environment mutableCopy];
    NSString *existingPath = environment[@"PATH"] ?: @"/usr/bin:/bin:/usr/sbin:/sbin";
    environment[@"PATH"] = [NSString stringWithFormat:@"%@:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin", existingPath];
    environment[@"PYTHONPATH"] = [NSBundle mainBundle].resourceURL.path;
    environment[@"PYTHONDONTWRITEBYTECODE"] = @"1";
    environment[@"FLOWLAB_DESKTOP_DIST"] = [self resourceURL:@"dist"].path;
    environment[@"FLOWLAB_RUNTIME_DIR"] = [support URLByAppendingPathComponent:@"runtime" isDirectory:YES].path;
    task.environment = environment;
    task.standardOutput = self.backendLog;
    task.standardError = self.backendLog;
    __weak typeof(self) weakSelf = self;
    task.terminationHandler = ^(NSTask *finished) {
        if (finished.terminationStatus == 0) return;
        dispatch_async(dispatch_get_main_queue(), ^{
            [weakSelf showError:@"The local solver service stopped. See ~/Library/Application Support/FlowLab/flowlab-backend.log"];
        });
    };
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [self showError:[NSString stringWithFormat:@"Could not start Python at %@. Set FLOWLAB_PYTHON to an environment containing FastAPI and Uvicorn.\n\n%@", [self pythonExecutable], error.localizedDescription]];
        return;
    }
    self.backend = task;
    self.startupTimer = [NSTimer scheduledTimerWithTimeInterval:0.2 target:self selector:@selector(pollBackend:) userInfo:nil repeats:YES];
}

- (void)pollBackend:(NSTimer *)timer {
    self.attempts += 1;
    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/api/health", (long)FlowLabPort]];
    NSURLSessionDataTask *request = [[NSURLSession sharedSession] dataTaskWithURL:url completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSInteger status = [(NSHTTPURLResponse *)response statusCode];
        if (status == 200) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [timer invalidate];
                NSURL *appURL = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/", (long)FlowLabPort]];
                [self.webView loadRequest:[NSURLRequest requestWithURL:appURL]];
            });
        } else if (self.attempts >= 75) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [timer invalidate];
                [self showError:@"The local solver service did not become ready. See ~/Library/Application Support/FlowLab/flowlab-backend.log"];
            });
        }
    }];
    [request resume];
}

- (void)showStatus:(NSString *)message {
    NSString *html = [NSString stringWithFormat:@"<!doctype html><meta charset='utf-8'><style>body{margin:0;background:#07111d;color:#dff7ff;font:15px -apple-system;display:grid;place-items:center;height:100vh}div{padding:28px;border:1px solid #29465c;border-radius:12px;background:#0b1928}</style><div>%@</div>", message];
    [self.webView loadHTMLString:html baseURL:nil];
}

- (void)showError:(NSString *)message {
    NSString *escaped = [[message stringByReplacingOccurrencesOfString:@"&" withString:@"&amp;"] stringByReplacingOccurrencesOfString:@"<" withString:@"&lt;"];
    escaped = [escaped stringByReplacingOccurrencesOfString:@"\n" withString:@"<br>"];
    NSString *html = [NSString stringWithFormat:@"<!doctype html><meta charset='utf-8'><style>body{margin:0;background:#160b0b;color:#ffe4d1;font:15px -apple-system;display:grid;place-items:center;height:100vh}div{max-width:720px;padding:28px;border:1px solid #8a4c35;border-radius:12px;background:#251313;line-height:1.5}</style><div><strong>FlowLab could not start</strong><br><br>%@</div>", escaped];
    [self.webView loadHTMLString:html baseURL:nil];
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        FlowLabAppDelegate *delegate = [[FlowLabAppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}

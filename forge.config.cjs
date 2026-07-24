"use strict";

const path = require("node:path");

const root = __dirname;
const version = require("./package.json").version;
const macSigningIdentity = process.env.FLOWLAB_MACOS_SIGN_IDENTITY;
const appleApiKey = process.env.FLOWLAB_APPLE_API_KEY;
const appleApiKeyId = process.env.FLOWLAB_APPLE_API_KEY_ID;
const appleApiIssuer = process.env.FLOWLAB_APPLE_API_ISSUER;
const windowsCertificateFile = process.env.FLOWLAB_WINDOWS_CERTIFICATE_FILE;
const windowsCertificatePassword = process.env.FLOWLAB_WINDOWS_CERTIFICATE_PASSWORD;

const macNotarizationConfigured = Boolean(appleApiKey && appleApiKeyId && appleApiIssuer);
const macEntitlements = path.join(root, "desktop", "electron", "entitlements.mac.plist");
const packageAllowlist = new Set([
  "/package.json",
  "/desktop",
  "/desktop/electron",
  "/desktop/electron/main.cjs",
  "/desktop/electron/preload.cjs",
  "/desktop/electron/runtime.cjs"
]);

module.exports = {
  packagerConfig: {
    name: "FlowLab",
    executableName: "FlowLab",
    appBundleId: "com.flowlab.desktop",
    appCategoryType: "public.app-category.developer-tools",
    win32metadata: {
      CompanyName: "FlowLab",
      FileDescription: "FlowLab local fluid-simulation workstation",
      InternalName: "FlowLab",
      OriginalFilename: "FlowLab.exe",
      ProductName: "FlowLab"
    },
    windowsSign: windowsCertificateFile
      ? {
          certificateFile: windowsCertificateFile,
          certificatePassword: windowsCertificatePassword,
          timestampServer: "http://timestamp.digicert.com"
        }
      : undefined,
    asar: true,
    prune: true,
    ignore(file) {
      const normalized = file.replaceAll("\\", "/");
      return normalized !== "" && !packageAllowlist.has(normalized);
    },
    extraResource: [
      path.join(root, "dist"),
      path.join(root, "build", "electron", "backend"),
      path.join(root, "desktop", "electron", "release-contract.json")
    ],
    osxSign: {
      identity: macSigningIdentity || "-",
      identityValidation: Boolean(macSigningIdentity),
      optionsForFile(filePath) {
        const isTopLevelApp = path.basename(filePath) === "FlowLab.app";
        return {
          // Ad-hoc identities do not share a stable Team ID across the
          // Electron framework tree. Applying the hardened-runtime option to
          // that internal build makes dyld reject Electron Framework even
          // though codesign verifies. Developer ID candidates retain it.
          hardenedRuntime: Boolean(macSigningIdentity),
          entitlements: macSigningIdentity && isTopLevelApp ? macEntitlements : undefined,
          timestamp: macSigningIdentity ? undefined : "none"
        };
      },
      continueOnError: false
    },
    osxNotarize: macNotarizationConfigured
      ? {
          tool: "notarytool",
          appleApiKey,
          appleApiKeyId,
          appleApiIssuer
        }
      : undefined
  },
  rebuildConfig: {},
  makers: [
    {
      name: "@electron-forge/maker-dmg",
      platforms: ["darwin"],
      config: {
        name: `FlowLab-${version}-macOS-arm64`,
        format: "ULFO",
        overwrite: true
      }
    },
    {
      name: "@electron-forge/maker-squirrel",
      platforms: ["win32"],
      config: {
        name: "FlowLab",
        authors: "FlowLab",
        description: "FlowLab local fluid-simulation workstation",
        setupExe: `FlowLab-${version}-Windows-x64-Setup.exe`,
        noMsi: true,
        certificateFile: windowsCertificateFile,
        certificatePassword: windowsCertificateFile ? windowsCertificatePassword : undefined
      }
    },
    {
      name: "@electron-forge/maker-zip",
      platforms: ["darwin", "win32"],
      config: {}
    }
  ]
};

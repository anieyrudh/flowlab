import type { StreamlineRequest, StreamlineResult, StreamlineWorkerRequest, StreamlineWorkerResponse } from "./types";

export type StreamlineWorkerRun = {
  promise: Promise<StreamlineResult>;
  cancel: () => void;
};

export function runStreamlinesInWorker(request: StreamlineRequest): StreamlineWorkerRun {
  const id = `streamline-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const worker = new Worker(new URL("./streamline.worker.ts", import.meta.url), { type: "module" });
  let rejectRun: (error: Error) => void = () => undefined;
  const promise = new Promise<StreamlineResult>((resolve, reject) => {
    rejectRun = reject;
    worker.onmessage = (event: MessageEvent<StreamlineWorkerResponse>) => {
      if (event.data.id !== id) return;
      worker.terminate();
      if (event.data.status === "complete") resolve(event.data.result);
      else reject(new Error(event.data.error));
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "Streamline worker failed."));
    };
    worker.postMessage({ ...request, id } satisfies StreamlineWorkerRequest);
  });
  return {
    promise,
    cancel: () => {
      worker.terminate();
      rejectRun(new Error("Streamline integration cancelled."));
    }
  };
}

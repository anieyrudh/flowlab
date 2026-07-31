/// <reference lib="webworker" />

import { integrateSteadyStreamlines } from "./core";
import type { StreamlineWorkerRequest, StreamlineWorkerResponse } from "./types";

const worker = self as DedicatedWorkerGlobalScope;

worker.onmessage = (event: MessageEvent<StreamlineWorkerRequest>) => {
  const request = event.data;
  try {
    const result = integrateSteadyStreamlines(request);
    worker.postMessage({ id: request.id, status: "complete", result } satisfies StreamlineWorkerResponse);
  } catch (error) {
    worker.postMessage({
      id: request.id,
      status: "error",
      error: error instanceof Error ? error.message : "Streamline integration failed."
    } satisfies StreamlineWorkerResponse);
  }
};

export {};

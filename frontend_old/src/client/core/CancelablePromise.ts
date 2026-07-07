/**
 * Compatibility shim for @hey-api/openapi-ts v0.94+
 * The new client no longer generates CancelablePromise; plain Promise is sufficient.
 */

export type CancelablePromise<T = unknown> = Promise<T>

/**
 * Web Push subscribe flow. Coordinates VAPID key fetch + browser
 * subscribe + server-side persist in one call.
 */

function _urlBase64ToUint8Array(base64) {
  // VAPID keys are base64url; convert to base64 + add padding.
  const padding = "=".repeat((4 - base64.length % 4) % 4);
  const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function _arrayBufferToBase64Url(buffer) {
  const bytes = new Uint8Array(buffer);
  let bin = "";
  for (let i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}


export async function subscribeForPush({ fetchFn = fetch, deviceLabel = "" } = {}) {
  // 1. Fetch the public VAPID key
  const keyResp = await fetchFn("/api/notifications/vapid-key");
  if (!keyResp.ok) throw new Error("Could not fetch VAPID key");
  const { public_key } = await keyResp.json();

  // 2. Wait for the service worker to be ready
  const reg = await navigator.serviceWorker.ready;

  // 3. Subscribe via the browser (idempotent — returns existing sub if present)
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: _urlBase64ToUint8Array(public_key),
    });
  }

  // 4. Build payload. PushSubscription.toJSON() returns base64url-encoded keys.
  const subJson = sub.toJSON ? sub.toJSON() : null;
  let p256dh, auth, endpoint;
  if (subJson && subJson.keys) {
    endpoint = subJson.endpoint;
    p256dh = subJson.keys.p256dh;
    auth = subJson.keys.auth;
  } else {
    endpoint = sub.endpoint;
    p256dh = _arrayBufferToBase64Url(sub.getKey("p256dh"));
    auth = _arrayBufferToBase64Url(sub.getKey("auth"));
  }

  // 5. POST to server
  const postResp = await fetchFn("/api/notifications/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint, p256dh, auth, device_label: deviceLabel }),
  });
  if (!postResp.ok) {
    const err = await postResp.json().catch(() => ({}));
    throw new Error(err.error || "Server rejected subscription");
  }
  return await postResp.json();
}

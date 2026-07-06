// api.js — thin fetch wrapper for the TALOS board API. The JWT is held in
// memory only (never localStorage/sessionStorage) so a page refresh requires
// re-login, per the P7a spec.

const API_BASE = ""; // same-origin — the API serves this page via StaticFiles

let _token = null;
let _onSessionExpired = null;

function setSessionExpiredHandler(fn) {
  _onSessionExpired = fn;
}

function isLoggedIn() {
  return _token !== null;
}

async function login(username, password) {
  const resp = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) {
    throw new Error("invalid credentials");
  }
  const body = await resp.json();
  _token = body.token;
  return _token;
}

function logout() {
  _token = null;
}

// Wraps fetch: attaches X-Human-Session when a token is held, and treats any
// 401/403 as an expired/invalid session — clears the token and calls back
// into the login screen. This is the single choke point for every screen's
// "expired-JWT relogin" behavior.
async function authFetch(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (_token) {
    headers["X-Human-Session"] = _token;
  }
  if (opts.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(`${API_BASE}${path}`, Object.assign({}, opts, { headers }));
  if (resp.status === 401 || resp.status === 403) {
    _token = null;
    if (_onSessionExpired) _onSessionExpired();
  }
  return resp;
}

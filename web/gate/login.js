// login.js — login screen logic.

function initLoginScreen() {
  const form = document.getElementById("login-form");
  const errorEl = document.getElementById("login-error");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    const username = document.getElementById("login-username").value;
    const password = document.getElementById("login-password").value;
    try {
      await login(username, password);
      document.getElementById("login-password").value = "";
      showQueueScreen();
    } catch (err) {
      errorEl.textContent = "Invalid credentials.";
    }
  });
}

function showLoginScreen() {
  stopQueuePolling();
  document.getElementById("screen-login").classList.remove("hidden");
  document.getElementById("screen-queue").classList.add("hidden");
  document.getElementById("screen-review").classList.add("hidden");
  document.getElementById("login-error").textContent = "";
}

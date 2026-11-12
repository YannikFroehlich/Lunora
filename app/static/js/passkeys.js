// Passkeys (WebAuthn). A form with data-passkey-form="create" (settings) or "get" (second login
// step, sign-in page) asks the browser for a credential on submit, puts the answer as JSON into its
// [data-passkey-response] field and then submits for real. The server made the options: a JSON
// script named by data-passkey-options, or (sign-in page, so a visit alone starts no session) the
// answer of a POST to data-passkey-options-url. It checks the answer: app/services/passkeys.py.
(function () {
  function fromBase64Url(text) {
    const binary = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  }

  function toBase64Url(buffer) {
    let binary = "";
    new Uint8Array(buffer).forEach((byte) => {
      binary += String.fromCharCode(byte);
    });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function withBinaryIds(credentials) {
    return (credentials || []).map((item) => ({ ...item, id: fromBase64Url(item.id) }));
  }

  async function createPasskey(options) {
    const credential = await navigator.credentials.create({
      publicKey: {
        ...options,
        challenge: fromBase64Url(options.challenge),
        user: { ...options.user, id: fromBase64Url(options.user.id) },
        excludeCredentials: withBinaryIds(options.excludeCredentials),
      },
    });
    return {
      id: credential.id,
      response: {
        clientDataJSON: toBase64Url(credential.response.clientDataJSON),
        attestationObject: toBase64Url(credential.response.attestationObject),
      },
    };
  }

  async function getPasskey(options) {
    const credential = await navigator.credentials.get({
      publicKey: {
        ...options,
        challenge: fromBase64Url(options.challenge),
        allowCredentials: withBinaryIds(options.allowCredentials),
      },
    });
    const { userHandle } = credential.response;
    return {
      id: credential.id,
      response: {
        clientDataJSON: toBase64Url(credential.response.clientDataJSON),
        authenticatorData: toBase64Url(credential.response.authenticatorData),
        signature: toBase64Url(credential.response.signature),
        userHandle: userHandle ? toBase64Url(userHandle) : null,
      },
    };
  }

  function errorText(error, mode) {
    if (error?.name === "InvalidStateError" && mode === "create") {
      return "Dieser Passkey ist auf dem Gerät schon eingerichtet.";
    }
    if (error?.name === "NotAllowedError") return "Abgebrochen oder Zeit abgelaufen. Bitte versuche es erneut.";
    return "Der Passkey konnte nicht verwendet werden.";
  }

  async function loadOptions(form) {
    if (!form.dataset.passkeyOptionsUrl) {
      return JSON.parse(document.getElementById(form.dataset.passkeyOptions).textContent);
    }
    // The form's own fields carry the CSRF token.
    const response = await fetch(form.dataset.passkeyOptionsUrl, { method: "POST", body: new FormData(form) });
    if (!response.ok) throw new Error("options request failed");
    return response.json();
  }

  const supported = Boolean(window.PublicKeyCredential && navigator.credentials);

  document.querySelectorAll("[data-passkey-form]").forEach((form) => {
    const status = form.querySelector("[data-passkey-status]");
    if (!supported) {
      form.querySelectorAll("button, input").forEach((control) => {
        control.disabled = true;
      });
      if (status) status.textContent = "Dieser Browser unterstützt keine Passkeys.";
      return;
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (form.getAttribute("aria-busy") === "true") return;
      const mode = form.dataset.passkeyForm;
      form.setAttribute("aria-busy", "true");
      if (status) status.textContent = "";
      try {
        const options = await loadOptions(form);
        const answer = await (mode === "create" ? createPasskey(options) : getPasskey(options));
        form.querySelector("[data-passkey-response]").value = JSON.stringify(answer);
        // submit() skips this listener; the page reloads with the server's verdict.
        form.submit();
      } catch (error) {
        form.removeAttribute("aria-busy");
        if (status) status.textContent = errorText(error, mode);
      }
    });
  });
})();

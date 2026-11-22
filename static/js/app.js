// Confirmation modals, double-submit guard and upload previews.
(function () {
  "use strict";

  // Modals are written next to the button that opens them (inside table rows, list rows and the navy
  // header), so they'd inherit that place's styles: white text, no wrapping, transforms. Move each one
  // to <body> so every modal looks the same wherever it was written.
  document.querySelectorAll(".modal").forEach(function (modal) {
    if (modal.parentElement !== document.body) document.body.appendChild(modal);
  });

  // Success and info messages close themselves after 5 seconds (not while the pointer is on them).
  // Warnings and errors stay until closed, so nobody misses them.
  document.querySelectorAll(".messages .alert-success, .messages .alert-info").forEach(function (alert) {
    var left = 5000, started = Date.now(), timer = null;
    function close() {
      if (window.bootstrap && bootstrap.Alert) bootstrap.Alert.getOrCreateInstance(alert).close();
      else alert.remove();
    }
    function start() { started = Date.now(); timer = setTimeout(close, left); }
    alert.addEventListener("mouseenter", function () { clearTimeout(timer); left = Math.max(1500, left - (Date.now() - started)); });
    alert.addEventListener("mouseleave", start);
    start();
  });

  // <img data-remove-if-missing>: drop the image if it fails to load (the drawn fallback shows instead).
  // Done here, not with onerror="", because the Content Security Policy blocks inline handlers.
  document.querySelectorAll("img[data-remove-if-missing]").forEach(function (img) {
    if (img.complete && img.naturalWidth === 0) img.remove();
    else img.addEventListener("error", function () { img.remove(); });
  });

  function disableButtons(form) {
    var buttons = Array.prototype.slice.call(form.querySelectorAll("button[type=submit], input[type=submit]"));
    if (form.id) {
      buttons = buttons.concat(Array.prototype.slice.call(
        document.querySelectorAll('[form="' + form.id + '"], [data-confirm-for="' + form.id + '"]')));
    }
    buttons.forEach(function (btn) { btn.disabled = true; });
  }

  // A form with data-confirm="#modal-id" opens that modal first (after the browser has
  // checked the required fields). The modal's [data-confirm-for] button then submits it.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var modalSelector = form.getAttribute("data-confirm");
    if (modalSelector && form.dataset.confirmed !== "1") {
      event.preventDefault();
      bootstrap.Modal.getOrCreateInstance(document.querySelector(modalSelector)).show();
      return;
    }
    if (form.dataset.submitted === "1") {
      event.preventDefault();
      return;
    }
    if (form.hasAttribute("data-once") || modalSelector) {
      form.dataset.submitted = "1";
      // Disable after the browser has collected the form data.
      setTimeout(function () { disableButtons(form); }, 0);
    }
  });

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-confirm-for]");
    if (!btn) return;
    var form = document.getElementById(btn.getAttribute("data-confirm-for"));
    if (!form || btn.disabled) return;
    btn.disabled = true;
    form.dataset.confirmed = "1";
    if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
  });

  // Buttons stay disabled if the page is restored from the back/forward cache: re-enable them.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    document.querySelectorAll("form[data-submitted]").forEach(function (form) {
      delete form.dataset.submitted;
      delete form.dataset.confirmed;
    });
    document.querySelectorAll("button[disabled][data-reenable], [data-confirm-for][disabled], form button[type=submit][disabled]")
      .forEach(function (b) { b.disabled = false; });
  });

  // Copy buttons: <button data-copy="text">Copy</button>
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-copy]");
    if (!btn || !navigator.clipboard) return;
    navigator.clipboard.writeText(btn.getAttribute("data-copy")).then(function () {
      var label = btn.textContent;
      btn.textContent = "Copied";
      btn.classList.add("copied");
      setTimeout(function () { btn.textContent = label; btn.classList.remove("copied"); }, 1500);
    });
  });

  // Mobile menu: the toggle adds .open to the top bar (styled in app.css).
  var navToggle = document.querySelector("[data-nav-toggle]");
  if (navToggle) {
    navToggle.addEventListener("click", function () {
      var open = document.getElementById("topbar").classList.toggle("open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // <select data-autosubmit> submits its form when the choice changes (e.g. the role filter).
  document.addEventListener("change", function (event) {
    if (event.target.matches("select[data-autosubmit]")) event.target.form.submit();
  });

  // <div data-show-if="field-id" data-show-value="X"> is shown only while that field's value is X.
  document.querySelectorAll("[data-show-if]").forEach(function (box) {
    var source = document.getElementById(box.getAttribute("data-show-if"));
    if (!source) return;
    var sync = function () { box.hidden = source.value !== box.getAttribute("data-show-value"); };
    source.addEventListener("change", sync);
    sync();
  });

  // <button data-print> opens the browser's print dialog (also "Save as PDF").
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-print]")) window.print();
  });

  // Upload preview: buttons with data-preview-url open the shared preview modal.
  var previewModal = document.getElementById("preview-modal");
  if (previewModal) {
    previewModal.addEventListener("show.bs.modal", function (event) {
      var trigger = event.relatedTarget;
      if (!trigger) return;
      var url = trigger.getAttribute("data-preview-url");
      var isPdf = trigger.getAttribute("data-preview-type") === "pdf";
      previewModal.querySelector(".modal-title").textContent = trigger.getAttribute("data-preview-title") || "Preview";
      previewModal.querySelector("[data-preview-open]").setAttribute("href", url);
      var body = previewModal.querySelector(".modal-body");
      body.innerHTML = "";
      // Files are fetched from private storage by the server, so show a loading note until they arrive.
      var status = document.createElement("div");
      status.className = "preview-status";
      status.textContent = "Loading the file…";
      body.appendChild(status);
      var el;
      if (isPdf) {
        el = document.createElement("iframe");
        el.className = "preview-frame";
        el.title = "File preview";
      } else {
        el = document.createElement("img");
        el.alt = "Uploaded file";
        el.onerror = function () { status.textContent = "Couldn't load the file. Try Open in new tab."; };
      }
      el.hidden = true;
      el.onload = function () { status.remove(); el.hidden = false; };
      el.src = url;
      body.appendChild(el);
    });
    previewModal.addEventListener("hidden.bs.modal", function () {
      previewModal.querySelector(".modal-body").innerHTML = "";
    });
  }
})();

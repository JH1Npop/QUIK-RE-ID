// Sign-up page: fill the mini ID preview as the student types.
(function () {
  "use strict";
  var form = document.getElementById("signup-form");
  if (!form) return;

  function update(name) {
    var target = document.querySelector('[data-preview="' + name + '"]');
    var input = form.elements[name];
    if (!target || !input) return;
    var value = input.tagName === "SELECT"
      ? (input.value ? input.options[input.selectedIndex].text : "")
      : input.value.trim();
    target.textContent = value || target.getAttribute("data-empty");
    target.classList.toggle("is-empty", !value);
  }

  ["full_name", "username", "lrn", "department"].forEach(function (name) {
    var input = form.elements[name];
    if (!input) return;
    input.addEventListener("input", function () { update(name); });
    input.addEventListener("change", function () { update(name); });
    update(name);   // also shows values kept after a failed submit
  });
})();

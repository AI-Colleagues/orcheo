const button = document.querySelector("#celebrate");
const status = document.querySelector("#status");

button?.addEventListener("click", () => {
  status.textContent = "Interaction confirmed. The published JavaScript is running.";
});

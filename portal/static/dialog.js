const agreement = document.querySelector("[data-agreement]");
const reviewButton = document.querySelector("[data-review]");
if (agreement && reviewButton && typeof agreement.showModal === "function") {
    agreement.close();
    reviewButton.hidden = false;
    reviewButton.addEventListener("click", () => agreement.showModal());
    agreement.showModal();
}

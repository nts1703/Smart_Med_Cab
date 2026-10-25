
document.addEventListener("DOMContentLoaded", () => {
    const slotSelect = document.getElementById("scheduleSlot");
    const selectedBox = document.getElementById(
        "selectedScheduleMedicine"
    );

    function updateSelectedMedicine() {
        if (!slotSelect || !selectedBox) return;

        const option = slotSelect.selectedOptions[0];

        if (!option || !option.value) {
            selectedBox.hidden = true;
            selectedBox.textContent = "";
            return;
        }

        const medicine = option.dataset.medicine || "";
        const strength = option.dataset.strength || "";
        const compartment = option.dataset.compartment || "";

        selectedBox.textContent = [
            "Đang tạo lịch cho: " + medicine,
            strength || "Chưa có hàm lượng",
            "Ngăn " + compartment
        ].join(" · ");

        selectedBox.hidden = false;
    }

    if (slotSelect) {
        slotSelect.addEventListener(
            "change", updateSelectedMedicine
        );
        updateSelectedMedicine();
    }

    const forms = document.querySelectorAll(
        "[data-schedule-form]"
    );

    forms.forEach((form) => {
        const startInput = form.querySelector(
            '[name="start_date"]'
        );
        const endInput = form.querySelector(
            '[name="end_date"]'
        );

        if (startInput && endInput) {
            startInput.addEventListener("change", () => {
                endInput.min = startInput.value;
                if (
                    endInput.value
                    && endInput.value < startInput.value
                ) {
                    endInput.value = "";
                }
            });
            endInput.min = startInput.value;
        }

        form.addEventListener("submit", (event) => {
            const days = form.querySelectorAll(
                'input[name="days"]:checked'
            );

            if (days.length === 0) {
                event.preventDefault();
                alert(
                    "Hãy chọn ít nhất một ngày nhắc trong tuần."
                );
                return;
            }

            if (
                startInput && endInput
                && endInput.value
                && endInput.value < startInput.value
            ) {
                event.preventDefault();
                alert(
                    "Ngày kết thúc không thể trước ngày bắt đầu."
                );
                endInput.focus();
                return;
            }

            const quantity = form.querySelector(
                '[name="dose_quantity"]'
            );
            if (
                quantity
                && (
                    !Number.isFinite(Number(quantity.value))
                    || Number(quantity.value) <= 0
                )
            ) {
                event.preventDefault();
                alert("Hãy nhập liều lượng hợp lệ.");
                quantity.focus();
            }
        });
    });
});

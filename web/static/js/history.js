
document.addEventListener("DOMContentLoaded", () => {
    const list = document.getElementById("historyList");
    const updated = document.getElementById("historyUpdated");
    const takenCount = document.getElementById("takenCount");
    const wrongCount = document.getElementById("wrongCount");
    const missedCount = document.getElementById("missedCount");

    const url = list.dataset.url;
    let loading = false;

    function createText(tag, className, value) {
        const element = document.createElement(tag);
        element.className = className;
        element.textContent = value;
        return element;
    }

    function render(items) {
        list.replaceChildren();

        if (!items.length) {
            list.appendChild(
                createText(
                    "div",
                    "history-empty",
                    "Chưa có sự kiện nào từ tủ thuốc."
                )
            );
        }

        let taken = 0;
        let wrong = 0;
        let missed = 0;

        items.forEach((item) => {
            if (item.event_type === "TAKEN") taken++;
            if (item.event_type === "WRONG") wrong++;
            if (item.event_type === "MISSED") missed++;

            const card = document.createElement("article");
            card.className = "history-item";

            const upper = document.createElement("div");
            upper.className = "history-item-upper";

            const title = createText(
                "strong",
                "history-medicine",
                item.medicine_name
            );

            const status = createText(
                "span",
                "history-badge status-" + item.event_type,
                item.label
            );

            upper.append(title, status);

            const details = createText(
                "div",
                "history-details",
                [
                    item.device_name,
                    "Ngăn " + item.compartment,
                    "Lịch #" + item.schedule_id
                ].join(" · ")
            );

            const time = createText(
                "div",
                "history-time",
                item.time + " · " + item.time_source
            );

            card.append(upper, details, time);
            list.appendChild(card);
        });

        takenCount.textContent = taken;
        wrongCount.textContent = wrong;
        missedCount.textContent = missed;
    }

    async function refresh() {
        if (loading) return;
        loading = true;

        try {
            const response = await fetch(url, {
                cache: "no-store",
                headers: {
                    "Accept": "application/json"
                }
            });

            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }

            const data = await response.json();

            if (!data.ok || !Array.isArray(data.items)) {
                throw new Error("Dữ liệu không hợp lệ");
            }

            render(data.items);

            updated.textContent =
                "Đã cập nhật: " +
                new Date().toLocaleTimeString("vi-VN");

        } catch (error) {
            updated.textContent =
                "Chưa thể cập nhật lịch sử: " +
                error.message;
        } finally {
            loading = false;
        }
    }

    refresh();
    setInterval(refresh, 5000);
});

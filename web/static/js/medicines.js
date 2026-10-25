
document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("addMedicineForm");
    if (!form) return;

    const modeCatalog = document.getElementById("modeCatalog");
    const modeManual = document.getElementById("modeManual");
    const catalogPanel = document.getElementById("catalogPanel");
    const manualPanel = document.getElementById("manualPanel");
    const queryInput = document.getElementById("medicineQuery");
    const selectedId = document.getElementById("selectedId");
    const selectedBox = document.getElementById("selectedMedicine");
    const results = document.getElementById("catalogResults");
    const loadMore = document.getElementById("loadMoreBtn");
    const manualName = document.getElementById("manualName");
    const searchUrl = results.dataset.searchUrl;

    let page = 1;
    let more = false;
    let timer = null;
    let controller = null;
    let requestNumber = 0;

    function setMode() {
        const manual = modeManual.checked;
        catalogPanel.hidden = manual;
        manualPanel.hidden = !manual;
        manualName.required = manual;
    }

    function clearSelection() {
        selectedId.value = "";
        selectedBox.textContent = "";
        selectedBox.hidden = true;
    }

    function showMessage(message) {
        const p = document.createElement("p");
        p.className = "med-empty";
        p.textContent = message;
        results.appendChild(p);
    }

    function selectDrug(drug) {
        selectedId.value = String(drug.id);
        selectedBox.textContent = [
            "Đã chọn: " + drug.name,
            drug.strength || "Chưa có hàm lượng",
            drug.dosage_form || "Chưa có dạng bào chế",
            drug.status_label
        ].join(" · ");
        selectedBox.hidden = false;
    }

    function renderDrug(drug) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "med-result-item";

        const top = document.createElement("div");
        top.className = "med-result-top";

        const name = document.createElement("strong");
        name.textContent = drug.name;

        const badge = document.createElement("span");
        badge.className = drug.verified
            ? "med-result-badge verified"
            : "med-result-badge";
        badge.textContent = drug.status_label;

        top.append(name, badge);

        const detail = document.createElement("div");
        detail.className = "med-result-detail";
        detail.textContent = [
            drug.strength || "Chưa có hàm lượng",
            drug.dosage_form || "Chưa có dạng bào chế",
            drug.registration_number
                ? "SĐK: " + drug.registration_number
                : "Chưa có SĐK"
        ].join(" · ");

        button.append(top, detail);
        button.addEventListener("click", () => selectDrug(drug));
        results.appendChild(button);
    }

    async function loadDrugs(reset = false) {
        if (reset) {
            page = 1;
            more = false;
            results.replaceChildren();
            loadMore.hidden = true;
            if (controller) controller.abort();
        }

        controller = new AbortController();
        const thisRequest = ++requestNumber;
        const keyword = queryInput.value.trim();
        const params = new URLSearchParams({
            q: keyword,
            page: String(page)
        });

        loadMore.disabled = true;
        if (reset) showMessage("Đang tìm kiếm...");

        try {
            const response = await fetch(
                searchUrl + "?" + params.toString(),
                {
                    signal: controller.signal,
                    headers: { "Accept": "application/json" }
                }
            );

            if (!response.ok || response.redirected) {
                throw new Error("Không thể tải dữ liệu thuốc.");
            }

            const data = await response.json();
            if (thisRequest !== requestNumber) return;

            if (reset) results.replaceChildren();

            if (data.items.length === 0 && page === 1) {
                showMessage(
                    "Không tìm thấy thuốc. Bạn có thể chuyển sang nhập tay."
                );
            }

            data.items.forEach(renderDrug);
            more = Boolean(data.has_more);
            loadMore.hidden = !more;
            page += 1;
        } catch (error) {
            if (error.name === "AbortError") return;
            if (thisRequest !== requestNumber) return;
            if (reset) results.replaceChildren();
            showMessage(
                "Không thể kết nối tới danh mục thuốc. "
                + "Hãy kiểm tra Flask và MySQL."
            );
            loadMore.hidden = true;
        } finally {
            if (thisRequest === requestNumber) {
                loadMore.disabled = false;
            }
        }
    }

    queryInput.addEventListener("input", () => {
        clearSelection();
        clearTimeout(timer);
        timer = setTimeout(() => loadDrugs(true), 250);
    });

    loadMore.addEventListener("click", () => loadDrugs(false));
    modeCatalog.addEventListener("change", setMode);
    modeManual.addEventListener("change", setMode);

    form.addEventListener("submit", (event) => {
        if (modeCatalog.checked && !selectedId.value) {
            event.preventDefault();
            alert("Bạn hãy chọn một thuốc trong danh sách gợi ý.");
            queryInput.focus();
        }
        if (modeManual.checked && !manualName.value.trim()) {
            event.preventDefault();
            alert("Bạn hãy nhập tên thuốc.");
            manualName.focus();
        }
    });

    setMode();
    loadDrugs(true);
});

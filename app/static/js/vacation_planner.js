(function () {
    const shell = document.querySelector("[data-vacation-preview-url]");
    if (!shell) {
        return;
    }

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const previewUrl = shell.dataset.vacationPreviewUrl;

    function setPreview(form, required, details) {
        const output = form.querySelector("[data-preview-output]");
        if (!output) {
            return;
        }
        output.querySelector("[data-preview-required]").textContent = required;
        output.querySelector("[data-preview-details]").textContent = details;
    }

    function overlapLabel(overlaps) {
        if (!overlaps.length) {
            return "";
        }
        const names = overlaps.map((overlap) => overlap.name).join(", ");
        return ` · Überschneidung: ${names}`;
    }

    async function updatePreview(form) {
        const startDate = form.querySelector('[name="start_date"]')?.value;
        const endDate = form.querySelector('[name="end_date"]')?.value;
        if (!startDate || !endDate) {
            setPreview(form, "-", "Start- und Enddatum wählen");
            return;
        }

        setPreview(form, "Berechne ...", "Feiertage und Wochenenden werden geprüft");
        const vacationId = form.querySelector('[name="period_id"]')?.value || null;
        try {
            const response = await fetch(previewUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken,
                },
                body: JSON.stringify({
                    start_date: startDate,
                    end_date: endDate,
                    vacation_id: vacationId,
                }),
            });
            const data = await response.json();
            if (!data.ok) {
                setPreview(form, "-", data.error || "Vorschau nicht verfügbar");
                return;
            }
            const missingYears = data.missing_years.length
                ? ` · fehlende Jahre: ${data.missing_years.join(", ")}`
                : "";
            setPreview(
                form,
                `${data.required_days_label} Tage`,
                `${data.calendar_days} Kalender · ${data.weekend_days} Wochenende · ${data.holiday_count} Feiertage${missingYears}${overlapLabel(data.overlaps)}`
            );
        } catch (error) {
            setPreview(form, "-", "Vorschau konnte nicht geladen werden");
        }
    }

    for (const form of document.querySelectorAll("[data-preview-form]")) {
        const trigger = () => updatePreview(form);
        form.querySelector('[name="start_date"]')?.addEventListener("change", trigger);
        form.querySelector('[name="end_date"]')?.addEventListener("change", trigger);
    }
})();

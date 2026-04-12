let canSubmit = true;

function applyDbcSelectMode() {
	const select = document.getElementById("dbc-select");
	const input = document.getElementById("dbc-input");
	if (!select || !input) return;
	const v = select.value;
	if (v === "custom") {
		input.disabled = false;
	} else {
		input.disabled = true;
		input.value = "";
		const label = document.getElementById("dbc-name-label");
		if (label) label.innerText = "";
	}
}

async function loadDbcList() {
	const select = document.getElementById("dbc-select");
	const hint = document.getElementById("dbc-list-hint");
	if (!select || !hint) return;
	select.innerHTML = "";
	hint.innerText = "Loading team DBC list…";
	try {
		const res = await fetch("/dbc/list");
		const data = await res.json();
		const optDefault = document.createElement("option");
		optDefault.value = "default";
		optDefault.textContent = "Default (container DBC)";
		const optCustom = document.createElement("option");
		optCustom.value = "custom";
		optCustom.textContent = "Custom upload…";

		if (!data.token_configured) {
			select.appendChild(optDefault);
			select.appendChild(optCustom);
			select.value = "default";
			hint.innerText =
				data.message ||
				"Set GITHUB_DBC_TOKEN on the server to list DBCs from Western-Formula-Racing/DBC.";
			applyDbcSelectMode();
			return;
		}

		if (data.error) {
			hint.innerText = data.error;
		} else {
			hint.innerText = "";
		}

		const items = data.items || [];
		for (const path of items) {
			const opt = document.createElement("option");
			opt.value = "github:" + path;
			opt.textContent = path;
			select.appendChild(opt);
		}
		select.appendChild(optCustom);

		if (items.length === 0) {
			select.value = "custom";
			hint.innerText =
				(hint.innerText ? hint.innerText + " " : "") +
				"No .dbc files in repo; upload a custom file.";
		} else if (items.length === 1) {
			select.value = "github:" + items[0];
		} else {
			select.value = "github:" + items[0];
		}
		applyDbcSelectMode();
	} catch (e) {
		console.error(e);
		hint.innerText = "Could not load team DBC list.";
		const optDefault = document.createElement("option");
		optDefault.value = "default";
		optDefault.textContent = "Default (container DBC)";
		const optCustom = document.createElement("option");
		optCustom.value = "custom";
		optCustom.textContent = "Custom upload…";
		select.appendChild(optDefault);
		select.appendChild(optCustom);
		select.value = "default";
		applyDbcSelectMode();
	}
}

function appendDbcToForm(form) {
	const select = document.getElementById("dbc-select");
	const dbcFile = document.getElementById("dbc-input")?.files?.[0];
	if (!select) return;
	const v = select.value;
	if (v.startsWith("github:")) {
		form.append("dbc_github_path", v.slice(7));
	} else if (v === "custom" && dbcFile) {
		form.append("dbc", dbcFile);
	}
}

async function parseUploadResponse(res) {
	const text = await res.text();
	try {
		return JSON.parse(text);
	} catch {
		return { error: text || res.statusText, _notJson: true };
	}
}

function submitCsvUpload(files) {
	const name_label = document.getElementById("file-name-label");
	const selected_season = document.getElementById("season-select").value;

	if (!selected_season || selected_season == "") {
		alert("Please select a season from the dropdown");
		return;
	}

	if (!files || files.length === 0) {
		name_label.innerText = "No Files Selected";
		name_label.style = "color: red;";
		alert("No Files Selected");
		return;
	}

	for (let i = 0; i < files.length; i++) {
		const file = files[i];
		const n = file.name.toLowerCase();
		const okCsv =
			file.type === "text/csv" ||
			n.endsWith(".csv") ||
			file.type === "application/csv";
		const okZip =
			n.endsWith(".zip") ||
			file.type === "application/zip" ||
			file.type === "application/x-zip-compressed";
		if (!okCsv && !okZip) {
			name_label.innerText = `File ${file.name} is not CSV or zip`;
			name_label.style = "color: red;";
			alert(`File ${file.name} must be .csv or .zip (of CSVs).`);
			return;
		}
	}

	const sel = document.getElementById("dbc-select");
	if (sel && sel.value === "custom") {
		const dbcFile = document.getElementById("dbc-input")?.files?.[0];
		const hasGithub = Array.from(sel.options).some((o) => o.value.startsWith("github:"));
		const hasDefault = Array.from(sel.options).some((o) => o.value === "default");
		if (!dbcFile) {
			if (hasGithub) {
				alert("Select a team DBC from the list, or choose a custom .dbc file.");
				return;
			}
			if (!hasDefault) {
				alert("Upload a custom .dbc file.");
				return;
			}
		}
	}

	const fileNames = Array.from(files).map((f) => f.name);
	if (files.length === 1) {
		name_label.innerText = fileNames[0];
	} else {
		name_label.innerText = `${files.length} CSV files: ${fileNames.slice(0, 3).join(", ")}${files.length > 3 ? "..." : ""}`;
	}
	name_label.style = "color: white;";

	const form = new FormData();
	for (let i = 0; i < files.length; i++) {
		form.append("file", files[i]);
	}
	form.append("season", selected_season);
	appendDbcToForm(form);

	fetch("/upload", {
		method: "POST",
		body: form,
	})
		.then((res) => parseUploadResponse(res))
		.then((data) => {
			if (data.error) {
				alert(data.error);
				location.reload();
				return;
			}
			if (data.task_id) {
				handleProgress(data.task_id);
				document.getElementById("task-id-label").innerText = data.task_id;
			} else {
				console.error("No task_id", data);
				name_label.innerText = "There was an error (check console)";
				name_label.style = "color: red;";
			}
		})
		.catch((err) => {
			console.error("error", err);
			name_label.innerText = "There was an error (check console)";
			name_label.style = "color: red;";
		});
}

document.addEventListener("DOMContentLoaded", () => {
	document.getElementById("drop_zone").addEventListener("drop", dropHandler);

	document
		.getElementById("drop_zone-input")
		.addEventListener("change", clickHandler);

	document
		.getElementById("drop_zone")
		.addEventListener("dragover", dragOverHandler);

	document.getElementById("dbc-input").addEventListener("change", (e) => {
		const file = e.target.files[0];
		document.getElementById("dbc-name-label").innerText = file ? file.name : "";
	});

	const dbcSelect = document.getElementById("dbc-select");
	if (dbcSelect) {
		dbcSelect.addEventListener("change", applyDbcSelectMode);
	}
	loadDbcList();

	if (document.getElementById("task-id-label").innerText) {
		handleProgress(document.getElementById("task-id-label").innerText);
	}
});

function clickHandler(e) {
	e.preventDefault();
	if (!canSubmit) {
		alert("File Currently Uploading");
		return;
	}
	submitCsvUpload(e.target.files);
}

function dropHandler(e) {
	e.preventDefault();
	if (!canSubmit) {
		alert("File Currently Uploading");
		return;
	}
	submitCsvUpload(e.dataTransfer?.files);
}

function dragOverHandler(e) {
	e.preventDefault();
}

function createBucket() {
	const name = document.getElementById("new-bucket-input").value.trim();
	const msg = document.getElementById("create-bucket-msg");
	const btn = document.getElementById("create-bucket-btn");
	if (!name) {
		msg.innerText = "Enter a name first.";
		msg.style.color = "salmon";
		return;
	}
	btn.disabled = true;
	msg.style.color = "";
	msg.innerText = "Creating...";
	fetch("/create-bucket", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ name }),
	})
		.then((res) => res.json())
		.then((data) => {
			if (data.error) {
				msg.innerText = data.error;
				msg.style.color = "salmon";
				btn.disabled = false;
				return;
			}
			const select = document.getElementById("season-select");
			const opt = document.createElement("option");
			opt.value = data.name;
			opt.innerText = data.name;
			opt.selected = true;
			select.appendChild(opt);
			document.getElementById("new-bucket-input").value = "";
			msg.innerText = "Created!";
			msg.style.color = "lightgreen";
			btn.disabled = false;
			setTimeout(() => {
				msg.innerText = "";
			}, 3000);
		})
		.catch((err) => {
			msg.innerText = "Error (check console)";
			msg.style.color = "salmon";
			btn.disabled = false;
			console.error(err);
		});
}

function handleProgress(task_id) {
	const eventSource = new EventSource(`/progress/${task_id}`);
	canSubmit = false;
	document.getElementById("drop_zone").innerHTML = `
		<svg class="spinner" viewBox="0 0 50 50">
							<circle
							class="path"
							cx="25"
							cy="25"
							r="20"
							fill="none"
							stroke-width="5"
							></circle>
						</svg>
						<h3>Currently Uploading File</h3> 
	`;
	eventSource.onmessage = (e) => {
		const data = JSON.parse(e.data);

		document.getElementById("progress-bar").style = `justify-content: baseline;`;
		document.getElementById("progress-bar").style = `width: ${data.pct}%;`;
		document.getElementById("progress-bar_pct").innerText = data.pct + "%";
		document.getElementById("progress-bar_count").innerText = `${data.sent} / ${data.total} rows`;

		document.getElementById("drop_zone-input").disabled = true;
		document.getElementById("season-select").disabled = true;
		const dbcSel = document.getElementById("dbc-select");
		if (dbcSel) dbcSel.disabled = true;
		const dbcIn = document.getElementById("dbc-input");
		if (dbcIn) dbcIn.disabled = true;

		if (data.done) {
			eventSource.close();
			document.getElementById("progress-bar_pct").innerText = "Done";
			document.getElementById("drop_zone-input").disabled = false;
			document.getElementById("season-select").disabled = false;
			if (dbcSel) dbcSel.disabled = false;
			if (dbcIn) dbcIn.disabled = false;
			applyDbcSelectMode();
			canSubmit = true;
			document.getElementById("drop_zone").innerHTML = `
			<svg
							id="file-upload-img"
							aria-hidden="true"
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 20 16"
						>
							<path
								stroke="currentColor"
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="2"
								d="M13 13h3a3 3 0 0 0 0-6h-.025A5.56 5.56 0 0 0 16 6.5 5.5 5.5 0 0 0 5.207 5.021C5.137 5.017 5.071 5 5 5a4 4 0 0 0 0 8h2.167M10 15V6m0 0L8 8m2-2 2 2"
							/>
						</svg>
						<h3>Click to upload CSV or zip, or drag and drop</h3>`;
			document.getElementById("drop_zone").addEventListener("drop", dropHandler);
			document.getElementById("drop_zone").addEventListener("dragover", dragOverHandler);
		}
	};
}

let canSubmit = true;

document.addEventListener("DOMContentLoaded", () => {
	document.getElementById("drop_zone").addEventListener("drop", dropHandler);

	document
		.getElementById("drop_zone-input")
		.addEventListener("change", clickHandler);

	document
		.getElementById("drop_zone")
		.addEventListener("dragover", dragOverHandler);

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
	console.log("file uploaded");
	const name_label = document.getElementById("file-name-label");
	const selected_bucket = document.getElementById("bucket-select").value;

	if (!selected_bucket || selected_bucket == "") {
		console.error("no bucket selected");
		alert("Please Select A Bucket From The Dropdown Menu");
		return;
	}

	const files = e.target.files;
	if (!files || files.length === 0) {
		console.error("no file");
		name_label.innerText = "No File Selected";
		name_label.style = "color: red;";
		alert("No File Selected");
		return;
	}

	const file = files[0];
	// console.log(file);
	// console.log(file.type);
	if (file.type !== "text/csv" && file.type !== "application/zip") {
		console.err("File not right type");
		name_label.innerText = "File can only be ZIP or CSV";
		name_label.style = "color: red;";
		alert("File can only be ZIP or CSV");
		return;
	}

	name_label.innerText = file.name;
	name_label.style = "color: white;";

	const form = new FormData();
	form.append("file", file);
	form.append("bucket", selected_bucket);

	fetch("/upload", {
		method: "POST",
		body: form,
	})
		.then((res) => res.json())
		.then((data) => {
			// console.log("response ", data);
			if (data.error) {
				alert(data.error);
				location.reload();
			}
			if (data.task_id) {
				handleProgress(data.task_id);
				document.getElementById("task-id-label").innerText = data.task_id;
			} else {
				console.error("No task_id");
			}
		})
		.catch((err) => {
			console.error("error", err);
			name_label.innerText = "There was an error (check console)";
			name_label.style = "color: red;";
		});
}

function dropHandler(e) {
	e.preventDefault();
	if (!canSubmit) {
		alert("File Currently Uploading");
		return;
	}
	console.log("file dropped");
	const name_label = document.getElementById("file-name-label");
	const selected_bucket = document.getElementById("bucket-select").value;

	if (!selected_bucket || selected_bucket == "") {
		console.error("no bucket selected");
		alert("Please Select A Bucket From The Dropdown Menu");
		return;
	}

	const files = e.dataTransfer?.files;
	if (!files || files.length === 0) {
		console.log("no file");
		name_label.innerText = "No File Selected";
		name_label.style = "color: red;";
		alert("No File Selected");
		return;
	}
	const file = files[0];
	// console.log(file);
	// console.log(file.type);
	if (file.type !== "text/csv" && file.type !== "application/zip") {
		console.log("File not right type");
		name_label.innerText = "File can only be ZIP or CSV";
		name_label.style = "color: red;";
		alert("File can only be ZIP or CSV");
		return;
	}

	name_label.innerText = file.name;
	name_label.style = "color: white;";

	const form = new FormData();
	form.append("file", file);
	form.append("bucket", selected_bucket);

	fetch("/upload", {
		method: "POST",
		body: form,
	})
		.then((res) => res.json())
		.then((data) => {
			// console.log("response ", data);
			if (data.error) {
				alert(data.error);
				location.reload();
			}

			if (data.task_id) {
				handleProgress(data.task_id);
				document.getElementById("task-id-label").innerText = data.task_id;
			} else {
				console.error("No task_id");
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

function dragOverHandler(e) {
	e.preventDefault();
}

function handleProgress(task_id) {
	console.log("running this");
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
		// console.log(data);

		document.getElementById(
			"progress-bar"
		).style = `justify-content: baseline;`;
		document.getElementById("progress-bar").style = `width: ${data.pct}%;`;
		document.getElementById("progress-bar_pct").innerText = data.pct + "%";
		document.getElementById(
			"progress-bar_count"
		).innerText = `${data.sent} / ${data.total} rows`;

		document.getElementById("drop_zone-input").disabled = true;
		document.getElementById("bucket-select").disabled = true;

		if (data.done) {
			eventSource.close();
			document.getElementById("progress-bar_pct").innerText = "Done";
			document.getElementById("drop_zone-input").disabled = false;
			document.getElementById("bucket-select").disabled = false;
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
						<h3>Click to upload or drag and drop</h3>`;
		}
	};
}

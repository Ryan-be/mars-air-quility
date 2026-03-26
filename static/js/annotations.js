export function attachAnnotationHandler(plotId) {
  document.getElementById(plotId).on('plotly_click', function(data) {
    const dbId = data.points[0].customdata;
    const timestamp = data.points[0].x;
    const currentAnnotation = data.points[0].text || "";

    const dialog = document.createElement("dialog");
    dialog.innerHTML = `
      <p><strong>Annotation</strong></p>
      <p>${timestamp}</p>
      <textarea id="annotationInput" rows="4">${currentAnnotation}</textarea>
      <div class="dialog-buttons">
        <button class="btn-save"   id="editBtn">Save</button>
        <button class="btn-delete" id="deleteBtn">Delete</button>
        <button class="btn-cancel" id="cancelBtn">Cancel</button>
      </div>`;
    document.body.appendChild(dialog);
    dialog.showModal();

    dialog.querySelector("#editBtn").onclick = () => {
      const newAnnotation = dialog.querySelector("#annotationInput").value;
      fetch(`/api/annotate?point=${dbId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ annotation: newAnnotation })
      }).then(() => { dialog.close(); dialog.remove(); });
    };
    dialog.querySelector("#deleteBtn").onclick = () => {
      fetch(`/api/annotate?point=${dbId}`, { method: "DELETE" })
        .then(() => { dialog.close(); dialog.remove(); });
    };
    dialog.querySelector("#cancelBtn").onclick = () => { dialog.close(); dialog.remove(); };
  });
}

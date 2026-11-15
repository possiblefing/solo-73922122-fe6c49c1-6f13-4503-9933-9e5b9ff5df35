(() => {
  "use strict";

  const MAX_STOPS = 6;
  const MAX_POINTS = 40;

  // ---------- 上传页：文件名回显与前端预检 ----------
  const form = document.getElementById("upload-form");
  if (form) {
    const hint = document.getElementById("client-hint");
    const inputs = ["station_file", "position_file"].map((id) => document.getElementById(id));

    inputs.forEach((input) => {
      if (!input) return;
      input.addEventListener("change", () => {
        const label = document.querySelector(`.file-name[data-for="${input.id}"]`);
        const file = input.files && input.files[0];
        if (label) label.textContent = file ? file.name : "未选择文件";
        input.closest(".drop")?.classList.toggle("has-file", Boolean(file));
      });
    });

    form.addEventListener("submit", (event) => {
      const missing = inputs.filter((input) => !input || !input.files || !input.files.length);
      if (missing.length) {
        event.preventDefault();
        if (hint) {
          hint.hidden = false;
          hint.textContent = "请同时选择站点 CSV 和定位 CSV。";
        }
        return;
      }
      const button = document.getElementById("submit-btn");
      if (button) {
        button.disabled = true;
        button.textContent = "分析中…";
      }
      if (hint) hint.hidden = true;
    });
  }

  // ---------- 结果页：在前端导出已确认事实 ----------
  const exportButton = document.getElementById("export-facts");
  if (exportButton && window.__RESULT__) {
    exportButton.addEventListener("click", () => {
      const facts = (window.__RESULT__.facts || []);
      const header = ["线路站序", "站名", "纬度", "经度", "半径米", "到站时刻UTC", "离站时刻UTC", "归站点数"];
      const escapeCell = (value) => {
        const text = String(value ?? "");
        return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
      };
      const lines = [header.map(escapeCell).join(",")];
      for (const fact of facts) {
        lines.push(
          [
            fact.route_index,
            fact.name,
            Number(fact.lat).toFixed(7),
            Number(fact.lon).toFixed(7),
            fact.radius_m,
            fact.arrival,
            fact.departure,
            fact.point_count,
          ].map(escapeCell).join(",")
        );
      }
      const blob = new Blob(["﻿" + lines.join("\r\n") + "\r\n"], {
        type: "text/csv;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "stop_facts.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
})();

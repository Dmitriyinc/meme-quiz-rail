/**
 * Google Apps Script — вставить в Extensions > Apps Script таблицы
 * Затем: Deploy > New deployment > Web app > Anyone > Deploy
 * Скопировать URL и вставить в .env как SHEETS_WEBHOOK
 *
 * Два листа:
 *   "Результаты" — финальные результаты квиза
 *   "Воронка"    — события: opened / started / completed
 */

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var ss = SpreadsheetApp.getActiveSpreadsheet();

    // Если это событие воронки
    if (data.type === "event") {
      var evSheet = ss.getSheetByName("Воронка");
      if (!evSheet) {
        evSheet = ss.insertSheet("Воронка");
        evSheet.appendRow(["Timestamp", "TG User ID", "Username", "Имя", "Событие"]);
        evSheet.getRange(1, 1, 1, 5).setFontWeight("bold").setBackground("#f3f3f3");
        evSheet.setFrozenRows(1);
      }
      evSheet.appendRow([
        new Date().toISOString(),
        data.user_id || "",
        data.username ? "@" + data.username : "",
        data.first_name || "",
        data.event || ""
      ]);
      return ContentService
        .createTextOutput(JSON.stringify({ ok: true }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // Результат квиза — лист "Результаты"
    var sheet = ss.getSheetByName("Результаты");
    if (!sheet) {
      sheet = ss.insertSheet("Результаты");
      sheet.appendRow([
        "Timestamp", "TG User ID", "Username", "Имя",
        "Скор", "Всего", "Результат", "Процент", "Время (сек)", "Разделы"
      ]);
      sheet.getRange(1, 1, 1, 10).setFontWeight("bold").setBackground("#f3f3f3");
      sheet.setFrozenRows(1);
    }

    var pct = Math.round((data.score / data.total) * 100);

    sheet.appendRow([
      data.timestamp || new Date().toISOString(),
      data.user_id,
      data.username ? "@" + data.username : "",
      data.first_name || "",
      data.score,
      data.total,
      data.passed ? "PASS" : "FAIL",
      pct + "%",
      data.time_spent || 0,
      data.sections || ""
    ]);

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ status: "ok", message: "Quiz webhook is live" }))
    .setMimeType(ContentService.MimeType.JSON);
}

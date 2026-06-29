/**
 * 外部連携得点（マークシートリーダー等）のインポート
 */

function importExternalScores(rows) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  var now = Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss');

  rows.forEach(function(r) {
    if (!r.studentId) return;
    sheet.appendRow([
      String(r.studentId),
      parseFloat(r.score) || 0,
      r.source || 'CSV取込',
      now
    ]);
  });

  applyExternalScoresToResults();
  return sheet.getLastRow() - 1;
}

function applyExternalScoresToResults() {
  var ss = getActiveTestSs();
  var extSheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  var resultSheet = ss.getSheetByName(SHEET_RESULTS);
  if (extSheet.getLastRow() <= 1 || resultSheet.getLastRow() <= 1) return 0;

  var extData = extSheet.getDataRange().getValues();
  var scoreByStudent = {};
  for (var i = 1; i < extData.length; i++) {
    var sid = String(extData[i][0]).trim();
    if (sid) scoreByStudent[sid] = parseFloat(extData[i][1]) || 0;
  }

  var headers = resultSheet.getRange(1, 1, 1, resultSheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var extCol = headers.indexOf('外部連携得点');
  var totalCol = headers.indexOf('総計点');
  if (extCol < 0) return 0;

  var data = resultSheet.getRange(2, 1, resultSheet.getLastRow() - 1, resultSheet.getLastColumn()).getValues();
  var fields = getAnswerFields(ss);
  var applied = 0;

  for (var r = 0; r < data.length; r++) {
    var sid = String(data[r][colMap.studentId] || '').trim();
    if (sid && scoreByStudent[sid] !== undefined) {
      data[r][extCol] = scoreByStudent[sid];
      applied++;

      if (totalCol >= 0) {
        var subtotal = 0;
        fields.forEach(function(f) {
          var label = f.displayName || f.id;
          var fm = colMap.fields[label];
          if (fm && fm.score >= 0) subtotal += parseInt(data[r][fm.score], 10) || 0;
        });
        data[r][totalCol] = subtotal + scoreByStudent[sid];
      }
    }
  }

  resultSheet.getRange(2, 1, data.length, headers.length).setValues(data);
  return applied;
}

function getExternalScores() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  if (sheet.getLastRow() <= 1) return [];
  var data = sheet.getDataRange().getValues();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    list.push({
      studentId: String(data[i][0]),
      score: parseFloat(data[i][1]) || 0,
      source: String(data[i][2] || ''),
      importedAt: data[i][3]
    });
  }
  return list;
}

function parseExternalScoresCsv(csvText) {
  var lines = String(csvText || '').split(/\r?\n/).filter(function(l) { return l.trim(); });
  var rows = [];
  lines.forEach(function(line, idx) {
    var parts = line.split(/[,;\t]/);
    if (parts.length < 2) return;
    if (idx === 0 && (parts[0].indexOf('ID') >= 0 || parts[0].indexOf('id') >= 0 || parts[0].indexOf('生徒') >= 0)) return;
    rows.push({
      studentId: parts[0].trim(),
      score: parseFloat(parts[1]) || 0,
      source: parts[2] ? parts[2].trim() : 'CSV取込'
    });
  });
  return rows;
}

function importExternalScoresFromCsv(csvText) {
  var rows = parseExternalScoresCsv(csvText);
  if (rows.length === 0) throw new Error('有効なCSVデータがありません。');
  return importExternalScores(rows);
}

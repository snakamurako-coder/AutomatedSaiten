/** データ行の読み書きヘルパー（getRange 4引数は numRows） */
function readDataRows(sheet) {
  var last = sheet.getLastRow();
  if (last <= 1) return [];
  return sheet.getRange(2, 1, last - 1, sheet.getLastColumn()).getValues();
}

function writeDataRows(sheet, data, numCols) {
  if (!data || data.length === 0) return;
  var cols = numCols || sheet.getLastColumn();
  sheet.getRange(2, 1, data.length, cols).setValues(data);
}

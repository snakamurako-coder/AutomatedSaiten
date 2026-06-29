/**
 * 領域設定・領域別得点集計
 */

function getDomainSettings(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_DOMAINS);
  var data = sheet.getDataRange().getValues();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][0]) continue;
    list.push({
      fieldId: String(data[i][0]),
      daiMon: data[i][1] != null ? String(data[i][1]) : '',
      hanI: data[i][2] != null ? String(data[i][2]) : '',
      noryoku: data[i][3] != null ? String(data[i][3]) : ''
    });
  }
  return list;
}

function saveDomainSettings(settings) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_DOMAINS);
  sheet.clear();
  sheet.appendRow(['記述欄ID', '大問', '範囲', '能力']);
  settings.forEach(function(s) {
    sheet.appendRow([s.fieldId, s.daiMon || '', s.hanI || '', s.noryoku || '']);
  });
  rebuildResultsSheetHeaders(ss);
  return getDomainSettings(ss);
}

function calculateDomainScores() {
  var ss = getActiveTestSs();
  var domains = getDomainSettings(ss);
  var fields = getAnswerFields(ss);
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return;

  rebuildResultsSheetHeaders(ss);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();

  var daiMonGroups = {}, hanIGroups = {}, noryokuGroups = {};
  domains.forEach(function(d) {
    if (d.daiMon) {
      if (!daiMonGroups[d.daiMon]) daiMonGroups[d.daiMon] = [];
      daiMonGroups[d.daiMon].push(d.fieldId);
    }
    if (d.hanI) {
      if (!hanIGroups[d.hanI]) hanIGroups[d.hanI] = [];
      hanIGroups[d.hanI].push(d.fieldId);
    }
    if (d.noryoku) {
      if (!noryokuGroups[d.noryoku]) noryokuGroups[d.noryoku] = [];
      noryokuGroups[d.noryoku].push(d.fieldId);
    }
  });

  function sumFieldScores(row, fieldIds) {
    var total = 0;
    fieldIds.forEach(function(fid) {
      var f = fields.find(function(x) { return x.id === fid; });
      if (!f) return;
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (fm && fm.score >= 0) total += parseInt(row[fm.score], 10) || 0;
    });
    return total;
  }

  for (var r = 0; r < data.length; r++) {
    var row = data[r];
    var subtotal = 0;

    Object.keys(daiMonGroups).forEach(function(k) {
      var colName = '大問' + k + '_得点';
      var idx = headers.indexOf(colName);
      if (idx >= 0) {
        var val = sumFieldScores(row, daiMonGroups[k]);
        row[idx] = val;
        subtotal += val;
      }
    });
    Object.keys(hanIGroups).forEach(function(k) {
      var colName = '範囲' + k + '_得点';
      var idx = headers.indexOf(colName);
      if (idx >= 0) row[idx] = sumFieldScores(row, hanIGroups[k]);
    });
    Object.keys(noryokuGroups).forEach(function(k) {
      var colName = '能力' + k + '_得点';
      var idx = headers.indexOf(colName);
      if (idx >= 0) row[idx] = sumFieldScores(row, noryokuGroups[k]);
    });

    fields.forEach(function(f) {
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (fm && fm.score >= 0) subtotal += parseInt(row[fm.score], 10) || 0;
    });

    var extIdx = headers.indexOf('外部連携得点');
    var extScore = extIdx >= 0 ? (parseFloat(row[extIdx]) || 0) : 0;
    var totalIdx = headers.indexOf('総計点');
    if (totalIdx >= 0) row[totalIdx] = subtotal + extScore;

    data[r] = row;
  }

  sheet.getRange(2, 1, data.length, headers.length).setValues(data);
  return data.length;
}

function getDomainSettingsForUi() {
  var ss = getActiveTestSs();
  var fields = getAnswerFields(ss);
  var domains = getDomainSettings(ss);
  var domainMap = {};
  domains.forEach(function(d) { domainMap[d.fieldId] = d; });

  return fields.map(function(f) {
    var d = domainMap[f.id] || {};
    return {
      fieldId: f.id,
      displayName: f.displayName || f.id,
      daiMon: d.daiMon || '',
      hanI: d.hanI || '',
      noryoku: d.noryoku || ''
    };
  });
}

/**
 * 採点基準・一括採点・考査総括
 */

function getUniqueAnswers(fieldId) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var headers = data[0];
  var fields = getAnswerFields(ss);
  var targetField = fields.find(function(f) { return f.id === fieldId; });
  if (!targetField) return [];

  var label = targetField.displayName || targetField.id;
  var textColName = label + '_テキスト';
  var colIndex = headers.indexOf(textColName);
  if (colIndex === -1) return [];

  var countMap = {};
  for (var i = 1; i < data.length; i++) {
    var answer = String(data[i][colIndex]).trim();
    if (!answer) answer = 'なし';
    countMap[answer] = (countMap[answer] || 0) + 1;
  }

  return Object.keys(countMap).map(function(key) {
    return { answer_text: key, count: countMap[key] };
  });
}

function generateRubricWithGemini(fieldId, uniqueAnswersArray) {
  var ss = getActiveTestSs();
  var pointsMap = getPointsMap(ss);
  var maxScore = pointsMap[fieldId] || 5;

  var apiKey = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!apiKey) throw new Error('GEMINI_API_KEY 未設定');

  var url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=' + apiKey;
  var prompt = {
    system_instruction: {
      parts: [{ text: 'あなたは厳格かつ公平なテスト採点基準を策定する専門家です。各解答に対し、○（満点）、△（部分点）、×（0点）の判定と付与得点（0〜満点の整数）および根拠をJSONで返してください。解答が「なし」の場合は×・0点としてください。' }]
    },
    contents: [{
      parts: [{ text: '記述欄ID: ' + fieldId + ', 満点: ' + maxScore + '点。ユニーク解答リスト:\n' + JSON.stringify(uniqueAnswersArray) }]
    }],
    generationConfig: {
      responseMimeType: 'application/json',
      responseSchema: {
        type: 'OBJECT',
        properties: {
          scrutinized_list: {
            type: 'ARRAY',
            items: {
              type: 'OBJECT',
              properties: {
                answer_text: { type: 'STRING' },
                judgment: { type: 'STRING' },
                recommended_score: { type: 'INTEGER' },
                reason: { type: 'STRING' }
              },
              required: ['answer_text', 'judgment', 'recommended_score', 'reason']
            }
          }
        },
        required: ['scrutinized_list']
      }
    }
  };

  var response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(prompt),
    muteHttpExceptions: true
  });
  var body = JSON.parse(response.getContentText());
  if (body.error) throw new Error('Gemini API: ' + JSON.stringify(body.error));
  return JSON.parse(body.candidates[0].content.parts[0].text);
}

function saveGradingCriteria(fieldId, confirmedRules) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_CRITERIA);
  var data = sheet.getDataRange().getValues();

  for (var i = data.length - 1; i >= 1; i--) {
    if (String(data[i][0]) === String(fieldId)) {
      sheet.deleteRow(i + 1);
    }
  }

  confirmedRules.forEach(function(rule) {
    sheet.appendRow([
      fieldId,
      rule.answer_text,
      rule.judgment || '×',
      parseInt(rule.score, 10) || 0,
      rule.reason || ''
    ]);
  });
  return true;
}

function getGradingCriteria(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_CRITERIA);
  var data = sheet.getDataRange().getValues();
  var rules = [];
  for (var i = 1; i < data.length; i++) {
    rules.push({
      fieldId: String(data[i][0]),
      answer_text: String(data[i][1]),
      judgment: String(data[i][2]),
      score: parseInt(data[i][3], 10) || 0,
      reason: String(data[i][4] || '')
    });
  }
  return rules;
}

function buildRuleMap(ss) {
  var rules = getGradingCriteria(ss);
  var ruleMap = {};
  rules.forEach(function(r) {
    if (!ruleMap[r.fieldId]) ruleMap[r.fieldId] = {};
    ruleMap[r.fieldId][String(r.answer_text).trim()] = {
      judgment: r.judgment,
      score: r.score
    };
  });
  return ruleMap;
}

function executeGrading() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) throw new Error('採点対象データがありません。');

  var fields = getAnswerFields(ss);
  var ruleMap = buildRuleMap(ss);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var unregisteredCount = 0;

  for (var r = 0; r < data.length; r++) {
    var row = data[r];
    fields.forEach(function(f) {
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (!fm) return;
      var answer = String(row[fm.text] || '').trim() || 'なし';
      var rule = ruleMap[f.id] && ruleMap[f.id][answer];
      if (rule) {
        row[fm.judgment] = rule.judgment;
        row[fm.score] = rule.score;
      } else {
        row[fm.judgment] = '×';
        row[fm.score] = 0;
        unregisteredCount++;
      }
    });
    data[r] = row;
  }

  sheet.getRange(2, 1, data.length, headers.length).setValues(data);
  calculateDomainScores();
  applyExternalScoresToResults();
  buildSummary(ss, unregisteredCount);
  updateTestStatus('採点完了');
  return { gradedCount: data.length, unregisteredCount: unregisteredCount };
}

function buildSummary(ss, unregisteredCount) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_SUMMARY);
  sheet.clear();
  sheet.appendRow(['区分', '項目', '値', '備考']);

  var resultSheet = ss.getSheetByName(SHEET_RESULTS);
  var headers = resultSheet.getRange(1, 1, 1, resultSheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = resultSheet.getLastRow() > 1
    ? resultSheet.getRange(2, 1, resultSheet.getLastRow() - 1, resultSheet.getLastColumn()).getValues()
    : [];
  var fields = getAnswerFields(ss);
  var studentCount = data.length;

  sheet.appendRow(['全体', '受験者数', studentCount, '']);
  sheet.appendRow(['全体', '未登録パターン照合数', unregisteredCount || 0, '採点基準に無い解答']);

  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    var fm = colMap.fields[label];
    if (!fm) return;
    var counts = { '○': 0, '△': 0, '×': 0, other: 0 };
    var totalScore = 0;
    data.forEach(function(row) {
      var j = String(row[fm.judgment] || '');
      if (counts[j] !== undefined) counts[j]++;
      else counts.other++;
      totalScore += parseInt(row[fm.score], 10) || 0;
    });
    var denom = studentCount || 1;
    sheet.appendRow(['設問', label + '_○人数', counts['○'], '']);
    sheet.appendRow(['設問', label + '_△人数', counts['△'], '']);
    sheet.appendRow(['設問', label + '_×人数', counts['×'], '']);
    sheet.appendRow(['設問', label + '_○率', Math.round(counts['○'] / denom * 1000) / 10 + '%', '']);
    sheet.appendRow(['設問', label + '_△率', Math.round(counts['△'] / denom * 1000) / 10 + '%', '']);
    sheet.appendRow(['設問', label + '_×率', Math.round(counts['×'] / denom * 1000) / 10 + '%', '']);
    sheet.appendRow(['設問', label + '_平均点', studentCount ? Math.round(totalScore / studentCount * 100) / 100 : 0, '']);
  });

  var domainLabels = getDomainColumnLabels(ss);
  domainLabels.forEach(function(dl) {
    var idx = headers.indexOf(dl);
    if (idx < 0) return;
    var maxPossible = getDomainMaxScore(ss, dl);
    var total = 0;
    data.forEach(function(row) { total += parseFloat(row[idx]) || 0; });
    var rate = studentCount && maxPossible ? Math.round(total / (studentCount * maxPossible) * 1000) / 10 : 0;
    sheet.appendRow(['領域', dl + '_平均', studentCount ? Math.round(total / studentCount * 100) / 100 : 0, '']);
    sheet.appendRow(['領域', dl + '_得点率', rate + '%', '満点合計=' + maxPossible]);
  });

  return sheet.getLastRow() - 1;
}

function getDomainMaxScore(ss, domainLabel) {
  var domains = getDomainSettings(ss);
  var fields = getAnswerFields(ss);
  var points = getPointsMap(ss);
  var match = domainLabel.match(/^(大問|範囲|能力)(.+)_得点$/);
  if (!match) return 0;
  var typeMap = { '大問': 'daiMon', '範囲': 'hanI', '能力': 'noryoku' };
  var key = match[2];
  var type = typeMap[match[1]];
  var total = 0;
  domains.forEach(function(d) {
    var fieldPoints = points[d.fieldId] || 0;
    if (type === 'daiMon' && String(d.daiMon) === key) total += fieldPoints;
    if (type === 'hanI' && String(d.hanI) === key) total += fieldPoints;
    if (type === 'noryoku' && String(d.noryoku) === key) total += fieldPoints;
  });
  return total;
}

function getSummaryData() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_SUMMARY);
  if (sheet.getLastRow() <= 1) return [];
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, 4).getValues().map(function(row) {
    return { category: row[0], item: row[1], value: row[2], note: row[3] };
  });
}

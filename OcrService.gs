/**
 * Vision API OCR・生徒解答処理
 */

function callVisionAPI(imageBytes) {
  var apiKey = PropertiesService.getScriptProperties().getProperty('VISION_API_KEY');
  if (!apiKey) throw new Error('VISION_API_KEY 未設定');
  var url = 'https://vision.googleapis.com/v1/images:annotate?key=' + apiKey;
  var payload = {
    requests: [{
      image: { content: imageBytes },
      features: [{ type: 'DOCUMENT_TEXT_DETECTION' }],
      imageContext: { languageHints: ['ja', 'en'] }
    }]
  };
  var response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  var json = JSON.parse(response.getContentText());
  if (json.error) throw new Error('Vision API: ' + JSON.stringify(json.error));
  if (!json.responses || !json.responses[0]) throw new Error('Vision API 応答が空です');
  return json.responses[0];
}

function extractTextFromBoxes(visionResult, targetBoxes) {
  if (!visionResult || !visionResult.textAnnotations) {
    return targetBoxes.map(function(box) {
      return { q_id: box.id, student_answer: 'なし' };
    });
  }
  var annotations = visionResult.textAnnotations;
  var result = [];
  targetBoxes.forEach(function(box) {
    var textInBox = [];
    for (var i = 1; i < annotations.length; i++) {
      var anno = annotations[i];
      var vertices = anno.boundingPoly ? anno.boundingPoly.vertices :
        (anno.boundingBox ? anno.boundingBox.vertices : anno.boundingVertice);
      if (!vertices || vertices.length < 4) continue;
      var cx = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4;
      var cy = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4;
      if (cx >= box.x && cx <= (box.x + box.w) && cy >= box.y && cy <= (box.y + box.h)) {
        textInBox.push({ text: anno.description, x: cx, y: cy });
      }
    }
    textInBox.sort(function(a, b) {
      if (Math.abs(a.y - b.y) > 15) return a.y - b.y;
      return a.x - b.x;
    });
    var finalString = textInBox.map(function(item) { return item.text; }).join('').trim();
    if (!finalString) finalString = 'なし';
    result.push({ q_id: box.id, student_answer: finalString });
  });
  return result;
}

function processStudentPaper(fileMeta, studentId, warpedBase64, skipIfExists) {
  try {
    var ss = getActiveTestSs();
    var sourceFileId = fileMeta.id || fileMeta.fileId;
    var sourceFileName = fileMeta.name || fileMeta.fileName || '';

    if (skipIfExists !== false && sourceFileId && isFileAlreadyProcessed(ss, sourceFileId)) {
      return { success: true, skipped: true, fileId: sourceFileId };
    }

    var fields = getAnswerFields(ss);
    if (fields.length === 0) throw new Error('記述欄が設定されていません。');

    var imageBytes = warpedBase64.split(',')[1];
    var saved = saveWarpedImage(warpedBase64, sourceFileName, studentId);
    if (sourceFileId && saved.fileId) {
      registerWarpedMapping(sourceFileId, saved.fileId);
    }

    var boxes = fieldsToBoxes(fields);
    var visionResult = callVisionAPI(imageBytes);
    var extracted = extractTextFromBoxes(visionResult, boxes);

    var textMapping = {};
    extracted.forEach(function(item) {
      textMapping[item.q_id] = item.student_answer;
    });

    var cleanStudentId = (studentId && !String(studentId).includes('?')) ? String(studentId) : '';

    appendResultRow(ss, {
      fileName: sourceFileName,
      fileId: sourceFileId,
      warpedFileId: saved.fileId
    }, cleanStudentId, textMapping);

    updateTestStatus('テキスト化中');
    return {
      success: true,
      studentId: cleanStudentId,
      fileId: sourceFileId,
      textMapping: textMapping,
      skipped: false
    };
  } catch (error) {
    return { success: false, error: error.toString(), fileId: fileMeta ? fileMeta.id : '' };
  }
}

function appendResultRow(ss, fileMeta, studentId, textMapping) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var fields = getAnswerFields(ss);
  if (sheet.getLastRow() === 0) {
    initResultsSheet(sheet, fields, getDynamicResultExtraColumns(ss));
  }

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var row = new Array(headers.length).fill('');

  var map = getResultColumnMap(headers);
  if (map.studentId >= 0) row[map.studentId] = studentId;
  if (map.fileName >= 0) row[map.fileName] = fileMeta.fileName || '';
  if (map.fileId >= 0) row[map.fileId] = fileMeta.fileId || '';

  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    var fieldMap = map.fields[label];
    if (!fieldMap) return;
    if (fieldMap.text >= 0) row[fieldMap.text] = textMapping[f.id] || 'なし';
  });

  sheet.appendRow(row);
}

function getResultRowCount() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  return Math.max(0, sheet.getLastRow() - 1);
}

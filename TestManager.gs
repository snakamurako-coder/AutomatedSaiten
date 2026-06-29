/**
 * テスト作成・選択・一覧
 */

function createTest(testName, subject, dateTime) {
  if (!testName || !String(testName).trim()) {
    throw new Error('テスト名は必須です。');
  }
  testName = String(testName).trim();
  initializeHub();
  var hubSs = getHubSs();
  setupHubSheets(hubSs);

  var ss = SpreadsheetApp.create(testName);
  buildTestSheets(ss);

  setTestInfoValue(ss, 'テスト名', testName);
  setTestInfoValue(ss, '科目名', subject || '');
  setTestInfoValue(ss, '実施日時', dateTime || '');
  setTestInfoValue(ss, '作成日時', Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss'));
  setTestInfoValue(ss, 'ステータス', '作成中');

  var folder = getOrCreateTestImageFolder(ss);
  setTestInfoValue(ss, '生徒解答フォルダID', folder.getId());

  var hubFile = DriveApp.getFileById(hubSs.getId());
  var parents = hubFile.getParents();
  if (parents.hasNext()) {
    DriveApp.getFileById(ss.getId()).moveTo(parents.next());
  }

  hubSs.getSheetByName(SHEET_HUB_TEST_LIST).appendRow([
    testName, ss.getId(), ss.getUrl(),
    Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss'),
    '作成中'
  ]);

  PropertiesService.getScriptProperties().setProperty('ACTIVE_TEST_SS_ID', ss.getId());

  return {
    testSsId: ss.getId(),
    url: ss.getUrl(),
    testName: testName,
    folderId: folder.getId()
  };
}

function listTests() {
  initializeHub();
  var hubSs = getHubSs();
  setupHubSheets(hubSs);
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var activeId = getActiveTestSsId();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    list.push({
      testName: data[i][0],
      testSsId: data[i][1],
      url: data[i][2],
      createdAt: data[i][3],
      status: data[i][4],
      isActive: data[i][1] === activeId
    });
  }
  return list;
}

function setActiveTest(testSsId) {
  if (!testSsId) throw new Error('テストIDが指定されていません。');
  SpreadsheetApp.openById(testSsId);
  PropertiesService.getScriptProperties().setProperty('ACTIVE_TEST_SS_ID', testSsId);
  return getTestInfo(testSsId);
}

function getTestInfo(testSsId) {
  var ss = testSsId ? SpreadsheetApp.openById(testSsId) : getActiveTestSs();
  return {
    testSsId: ss.getId(),
    url: ss.getUrl(),
    info: getTestInfoObject(ss),
    fields: getAnswerFields(ss),
    points: getPointsMap(ss),
    activeTestSsId: ss.getId()
  };
}

function updateTestStatus(status) {
  var ss = getActiveTestSs();
  setTestInfoValue(ss, 'ステータス', status);
  updateHubTestStatus(ss.getId(), status);
  return true;
}

function updateHubTestStatus(testSsId, status) {
  var hubSs = getHubSs();
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (data[i][1] === testSsId) {
      sheet.getRange(i + 1, 5).setValue(status);
      return;
    }
  }
}

function saveStudentFolderId(folderId) {
  var ss = getActiveTestSs();
  setTestInfoValue(ss, '生徒解答フォルダID', folderId);
  return true;
}

function saveModelAnswerImage(base64Image, width, height) {
  var ss = getActiveTestSs();
  var imageBytes = base64Image.split(',')[1];
  var folder = getOrCreateTestImageFolder(ss);
  var fileName = '模範解答_' + Utilities.formatDate(new Date(), 'JST', 'yyyyMMdd_HHmmss') + '.jpg';
  var oldId = getTestInfoValue(ss, '模範解答画像FileID');
  if (oldId) {
    try { DriveApp.getFileById(oldId).setTrashed(true); } catch (e) { /* ignore */ }
  }
  var file = folder.createFile(Utilities.newBlob(Utilities.base64Decode(imageBytes), 'image/jpeg', fileName));
  setTestInfoValue(ss, '模範解答画像FileID', file.getId());
  if (width) setTestInfoValue(ss, '基準画像幅', width);
  if (height) setTestInfoValue(ss, '基準画像高さ', height);
  return { fileId: file.getId(), fileName: fileName };
}

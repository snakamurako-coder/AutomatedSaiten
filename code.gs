/**
 * 模範解答ベース自動採点システム — サーバー側（code.gs）
 */

var HUB_SS_ID_KEY = 'HUB_SS_ID';

function doGet(e) {
  try {
    applyHubIdFromRequest_(e);
    initializeHub();
  } catch (err) {
    return HtmlService.createHtmlOutput(
      '<div style="font-family:sans-serif;padding:2em;max-width:640px">' +
      '<h2>初期設定が必要です</h2>' +
      '<p>' + err.message + '</p>' +
      '<p>ハブ用スプレッドシートを開き、メニュー「自動採点 → Webアプリを開く」（または「ハブを登録」）を実行してから再度アクセスしてください。</p>' +
      '</div>'
    ).setTitle('模範解答ベース自動採点システム');
  }
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('模範解答ベース自動採点システム')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function applyHubIdFromRequest_(e) {
  if (!e || !e.parameter) return;
  var hubId = e.parameter.hubId || e.parameter.hubSsId;
  if (!hubId) return;
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, hubId);
  try {
    setupHubSheets(SpreadsheetApp.openById(hubId));
  } catch (err) { /* hubId invalid */ }
}

function onOpen() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (ss) {
    PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
    setupHubSheets(ss);
    try { syncHubTestList(); } catch (e) { /* ignore on first open */ }
  }
  SpreadsheetApp.getUi()
    .createMenu('自動採点')
    .addItem('Webアプリを開く', 'openWebAppFromMenu')
    .addItem('ハブを登録', 'registerHubSpreadsheet')
    .addItem('テスト一覧を再同期', 'syncHubTestListFromMenu')
    .addItem('古いWARP設定を削除', 'cleanupWarpScriptProperties')
    .addToUi();
}

function openWebAppFromMenu() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('スプレッドシートを開いた状態で実行してください。');
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
  setupHubSheets(ss);
  syncHubTestList();
  var url = ScriptApp.getService().getUrl();
  if (!url) {
    SpreadsheetApp.getUi().alert('Webアプリが未デプロイです。「デプロイ」→「新しいデプロイ」で Web アプリを公開してください。');
    return;
  }
  var sep = url.indexOf('?') >= 0 ? '&' : '?';
  var openUrl = url + sep + 'hubId=' + encodeURIComponent(ss.getId());
  var html = HtmlService.createHtmlOutput(
    '<p style="font-family:sans-serif;font-size:13px">アプリを開いています…</p>' +
    '<script>window.open("' + openUrl + '","_blank");google.script.host.close();</script>'
  ).setWidth(260).setHeight(90);
  SpreadsheetApp.getUi().showModalDialog(html, '自動採点アプリ');
}

function syncHubTestListFromMenu() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('スプレッドシートを開いた状態で実行してください。');
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
  setupHubSheets(ss);
  var n = syncHubTestList();
  SpreadsheetApp.getUi().alert('テスト一覧を再同期しました（' + n + ' 件）。');
}

function registerHubSpreadsheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('スプレッドシートを開いた状態で実行してください。');
  PropertiesService.getScriptProperties().setProperty(HUB_SS_ID_KEY, ss.getId());
  setupHubSheets(ss);
  initializeHub();
  var n = syncHubTestList();
  SpreadsheetApp.getUi().alert('ハブを登録しました。\n' + ss.getUrl() + '\n\nテスト一覧: ' + n + ' 件を同期');
  return { hubSsId: ss.getId(), url: ss.getUrl() };
}

function getHubSs() {
  var props = PropertiesService.getScriptProperties();
  var hubId = props.getProperty(HUB_SS_ID_KEY);
  if (hubId) {
    try {
      return SpreadsheetApp.openById(hubId);
    } catch (e) {
      props.deleteProperty(HUB_SS_ID_KEY);
    }
  }
  var active = SpreadsheetApp.getActiveSpreadsheet();
  if (active) {
    props.setProperty(HUB_SS_ID_KEY, active.getId());
    return active;
  }
  throw new Error('ハブ用スプレッドシートが未登録です。スプレッドシートを開き「自動採点 → ハブを登録」を実行してください。');
}

function initializeHub() {
  var properties = PropertiesService.getScriptProperties();
  var ss = getHubSs();
  setupHubSheets(ss);

  var rootFolderId = properties.getProperty('ROOT_IMAGE_FOLDER_ID');
  if (rootFolderId) {
    try {
      DriveApp.getFolderById(rootFolderId);
      return;
    } catch (e) { /* recreate */ }
  }

  var file = DriveApp.getFileById(ss.getId());
  var parents = file.getParents();
  if (!parents.hasNext()) throw new Error('親フォルダの取得に失敗しました。');

  var parentFolder = parents.next();
  var subFolders = parentFolder.getFoldersByName('採点システム画像');
  var rootFolder = subFolders.hasNext() ? subFolders.next() : parentFolder.createFolder('採点システム画像');
  properties.setProperty('ROOT_IMAGE_FOLDER_ID', rootFolder.getId());
}

function setupHubSheets(ss) {
  if (!ss.getSheetByName(SHEET_HUB_TEST_LIST)) {
    var sheet = ss.insertSheet(SHEET_HUB_TEST_LIST);
    sheet.appendRow(['テスト名', 'スプレッドシートID', 'URL', '作成日', 'ステータス', '現在ステップ', '最終保存日時']);
    sheet.setFrozenRows(1);
  } else {
    ensureHubSheetColumns(ss.getSheetByName(SHEET_HUB_TEST_LIST));
  }
  var sheet1 = ss.getSheetByName('シート1');
  if (sheet1 && ss.getSheets().length > 1 && sheet1.getLastRow() === 0) {
    ss.deleteSheet(sheet1);
  }
}

function getActiveTestSs() {
  var id = PropertiesService.getScriptProperties().getProperty('ACTIVE_TEST_SS_ID');
  if (!id) throw new Error('アクティブなテストが選択されていません。テストを作成または選択してください。');
  return SpreadsheetApp.openById(id);
}

function getActiveTestSsId() {
  return PropertiesService.getScriptProperties().getProperty('ACTIVE_TEST_SS_ID') || '';
}

function getTestImageRootFolder() {
  var properties = PropertiesService.getScriptProperties();
  var rootId = properties.getProperty('ROOT_IMAGE_FOLDER_ID');
  if (!rootId) {
    initializeHub();
    rootId = properties.getProperty('ROOT_IMAGE_FOLDER_ID');
  }
  return DriveApp.getFolderById(rootId);
}

function getOrCreateTestImageFolder(ss) {
  var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  if (folderId) {
    try {
      return DriveApp.getFolderById(folderId);
    } catch (e) { /* recreate */ }
  }
  var testName = getTestInfoValue(ss, 'テスト名') || ss.getName();
  var root = getTestImageRootFolder();
  var sub = root.createFolder(testName + '_' + ss.getId().substring(0, 8));
  setTestInfoValue(ss, '生徒解答フォルダID', sub.getId());
  return sub;
}




// ========== SheetBuilder.gs ==========

/**
 * シート名定数・テスト用スプレッドシート構築
 */

var SHEET_HUB_TEST_LIST = 'テスト一覧';

var SHEET_TEST_INFO = 'テスト情報';
var SHEET_ANSWER_FIELDS = '記述欄情報';
var SHEET_POINTS = '配点情報';
var SHEET_RESULTS = '採点結果';
var SHEET_CRITERIA = '採点基準';
var SHEET_SUMMARY = '考査総括';
var SHEET_DOMAINS = '領域設定';
var SHEET_IDENTITY_FIELDS = '本人確認欄情報';
var SHEET_EXTERNAL_SCORES = '外部連携得点';
var SHEET_OCR_REPLACEMENTS = 'OCR置換ルール';

var TEST_INFO_KEYS = [
  'テスト名', '科目名', '実施日時', '作成日時',
  '模範解答画像FileID', '生徒解答フォルダID',
  '基準画像幅', '基準画像高さ', 'ステータス',
  '現在ステップ', '最終保存日時'
];

var HUB_TEST_LIST_HEADERS = ['テスト名', 'スプレッドシートID', 'URL', '作成日', 'ステータス', '現在ステップ', '最終保存日時'];

function ensureHubSheetColumns(sheet) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  HUB_TEST_LIST_HEADERS.forEach(function(h) {
    if (headers.indexOf(h) < 0) {
      sheet.getRange(1, headers.length + 1).setValue(h);
      headers.push(h);
    }
  });
}

function ensureTestInfoKeys(ss) {
  TEST_INFO_KEYS.forEach(function(key) {
    if (getTestInfoValue(ss, key) === '' && key !== 'テスト名') {
      var sheet = ss.getSheetByName(SHEET_TEST_INFO);
      var found = false;
      var data = sheet.getDataRange().getValues();
      for (var i = 0; i < data.length; i++) {
        if (data[i][0] === key) { found = true; break; }
      }
      if (!found) sheet.appendRow([key, '']);
    }
  });
}

function buildTestSheets(ss) {
  Object.values(getAllTestSheetNames()).forEach(function(name) {
    if (!ss.getSheetByName(name)) {
      ss.insertSheet(name);
    }
  });

  initTestInfoSheet(ss.getSheetByName(SHEET_TEST_INFO));
  initAnswerFieldsSheet(ss.getSheetByName(SHEET_ANSWER_FIELDS));
  initPointsSheet(ss.getSheetByName(SHEET_POINTS));
  initResultsSheet(ss.getSheetByName(SHEET_RESULTS), []);
  initCriteriaSheet(ss.getSheetByName(SHEET_CRITERIA));
  initSummarySheet(ss.getSheetByName(SHEET_SUMMARY));
  initDomainsSheet(ss.getSheetByName(SHEET_DOMAINS));
  initIdentityFieldsSheet(ss.getSheetByName(SHEET_IDENTITY_FIELDS));
  initExternalScoresSheet(ss.getSheetByName(SHEET_EXTERNAL_SCORES));
  ensureOcrReplacementsSheet(ss);

  const defaultSheet = ss.getSheetByName('シート1');
  if (defaultSheet) ss.deleteSheet(defaultSheet);
  ss.setActiveSheet(ss.getSheetByName(SHEET_TEST_INFO));
}

function getAllTestSheetNames() {
  return {
    TEST_INFO: SHEET_TEST_INFO,
    ANSWER_FIELDS: SHEET_ANSWER_FIELDS,
    POINTS: SHEET_POINTS,
    RESULTS: SHEET_RESULTS,
    CRITERIA: SHEET_CRITERIA,
    SUMMARY: SHEET_SUMMARY,
    DOMAINS: SHEET_DOMAINS,
    IDENTITY_FIELDS: SHEET_IDENTITY_FIELDS,
    EXTERNAL_SCORES: SHEET_EXTERNAL_SCORES,
    OCR_REPLACEMENTS: SHEET_OCR_REPLACEMENTS
  };
}

function ensureOcrReplacementsSheet(ss) {
  if (!ss.getSheetByName(SHEET_OCR_REPLACEMENTS)) {
    var sheet = ss.insertSheet(SHEET_OCR_REPLACEMENTS);
    sheet.appendRow(['記述欄ID', '検索文字列', '置換後', '正規表現']);
    sheet.setFrozenRows(1);
  }
}

function initOcrReplacementsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '検索文字列', '置換後', '正規表現']);
  sheet.setFrozenRows(1);
}

function initTestInfoSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  TEST_INFO_KEYS.forEach(function(key) {
    sheet.appendRow([key, '']);
  });
  sheet.setColumnWidth(1, 200);
  sheet.setColumnWidth(2, 350);
}

function initAnswerFieldsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '表示名', 'x', 'y', 'width', 'height', '表示順']);
  sheet.setFrozenRows(1);
}

function initPointsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '満点']);
  sheet.setFrozenRows(1);
}

function initCriteriaSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '解答パターン', '判定', '付与得点', '備考']);
  sheet.setFrozenRows(1);
}

function initSummarySheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['区分', '項目', '値', '備考']);
  sheet.setFrozenRows(1);
}

function initDomainsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['記述欄ID', '大問', '範囲', '能力']);
  sheet.setFrozenRows(1);
}

function initIdentityFieldsSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['欄種別', 'x', 'y', 'width', 'height']);
  sheet.setFrozenRows(1);
}

function initExternalScoresSheet(sheet) {
  if (sheet.getLastRow() > 0) return;
  sheet.appendRow(['生徒ID', '外部得点', 'ソース', 'インポート日時']);
  sheet.setFrozenRows(1);
}

function buildResultHeaders(fields, extraColumns) {
  var headers = ['生徒ID', 'ファイル名', 'ファイルID', '補正画像FileID', '氏名'];
  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    headers.push(label + '_テキスト');
    headers.push(label + '_判定');
    headers.push(label + '_得点');
  });
  (extraColumns || []).forEach(function(col) {
    headers.push(col);
  });
  return headers;
}

function initResultsSheet(sheet, fields, extraColumns) {
  var headers = buildResultHeaders(fields, extraColumns);
  sheet.clear();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.setFrozenRows(1);
}

function rebuildResultsSheetHeaders(ss) {
  var fields = getAnswerFields(ss);
  var extra = getDynamicResultExtraColumns(ss);
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var oldData = [];
  if (sheet.getLastRow() > 1) {
    var numRows = sheet.getLastRow() - 1;
    var numCols = sheet.getLastColumn();
    oldData = sheet.getRange(2, 1, numRows, numCols).getValues();
  }
  var oldHeaders = sheet.getLastRow() >= 1 ? sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0] : [];
  initResultsSheet(sheet, fields, extra);
  if (oldData.length === 0) return;

  var newHeaders = buildResultHeaders(fields, extra);
  oldData.forEach(function(row) {
    var newRow = mapResultRow(oldHeaders, row, newHeaders);
    sheet.appendRow(newRow);
  });
}

function getDynamicResultExtraColumns(ss) {
  var cols = [];
  var domainLabels = getDomainColumnLabels(ss);
  domainLabels.forEach(function(l) { cols.push(l); });
  cols.push('外部連携得点');
  cols.push('総計点');
  return cols;
}

function getDomainColumnLabels(ss) {
  var sheet = ss.getSheetByName(SHEET_DOMAINS);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  var data = sheet.getDataRange().getValues();
  var daiMon = {}, hanI = {}, noryoku = {};
  for (var i = 1; i < data.length; i++) {
    if (data[i][1]) daiMon[String(data[i][1])] = true;
    if (data[i][2]) hanI[String(data[i][2])] = true;
    if (data[i][3]) noryoku[String(data[i][3])] = true;
  }
  var labels = [];
  Object.keys(daiMon).sort().forEach(function(k) { labels.push('大問' + k + '_得点'); });
  Object.keys(hanI).sort().forEach(function(k) { labels.push('範囲' + k + '_得点'); });
  Object.keys(noryoku).sort().forEach(function(k) { labels.push('能力' + k + '_得点'); });
  return labels;
}

function getResultColumnMap(headers) {
  var map = {
    studentId: headers.indexOf('生徒ID'),
    fileName: headers.indexOf('ファイル名'),
    fileId: headers.indexOf('ファイルID'),
    warpedFileId: headers.indexOf('補正画像FileID'),
    name: headers.indexOf('氏名'),
    fields: {},
    extras: {}
  };
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]);
    var textMatch = h.match(/^(.+)_テキスト$/);
    var judgeMatch = h.match(/^(.+)_判定$/);
    var scoreMatch = h.match(/^(.+)_得点$/);
    if (textMatch) {
      if (!map.fields[textMatch[1]]) map.fields[textMatch[1]] = {};
      map.fields[textMatch[1]].text = i;
    } else if (judgeMatch) {
      if (!map.fields[judgeMatch[1]]) map.fields[judgeMatch[1]] = {};
      map.fields[judgeMatch[1]].judgment = i;
    } else if (scoreMatch) {
      if (!map.fields[scoreMatch[1]]) map.fields[scoreMatch[1]] = {};
      map.fields[scoreMatch[1]].score = i;
    } else if (h === '外部連携得点') {
      map.extras.external = i;
    } else if (h === '総計点') {
      map.extras.total = i;
    } else if (h.indexOf('_得点') > -1) {
      map.extras[h] = i;
    }
  }
  return map;
}

function mapResultRow(oldHeaders, oldRow, newHeaders) {
  var newRow = new Array(newHeaders.length).fill('');
  for (var i = 0; i < oldHeaders.length; i++) {
    var idx = newHeaders.indexOf(oldHeaders[i]);
    if (idx >= 0) newRow[idx] = oldRow[i];
  }
  return newRow;
}

function columnIndexToLetter(column) {
  var letter = '';
  while (column > 0) {
    var mod = (column - 1) % 26;
    letter = String.fromCharCode(65 + mod) + letter;
    column = Math.floor((column - 1) / 26);
  }
  return letter;
}

function getTestInfoValue(ss, key) {
  var sheet = ss.getSheetByName(SHEET_TEST_INFO);
  var data = sheet.getDataRange().getValues();
  for (var i = 0; i < data.length; i++) {
    if (data[i][0] === key) return data[i][1] != null ? String(data[i][1]) : '';
  }
  return '';
}

function setTestInfoValue(ss, key, value) {
  var sheet = ss.getSheetByName(SHEET_TEST_INFO);
  var data = sheet.getDataRange().getValues();
  for (var i = 0; i < data.length; i++) {
    if (data[i][0] === key) {
      sheet.getRange(i + 1, 2).setValue(value);
      return;
    }
  }
  sheet.appendRow([key, value]);
}

function getTestInfoObject(ss) {
  var obj = {};
  TEST_INFO_KEYS.forEach(function(k) {
    obj[k] = getTestInfoValue(ss, k);
  });
  return obj;
}


// ========== TestManager.gs ==========

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

  ensureHubSheetColumns(hubSs.getSheetByName(SHEET_HUB_TEST_LIST));
  hubSs.getSheetByName(SHEET_HUB_TEST_LIST).appendRow([
    testName, ss.getId(), ss.getUrl(),
    Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss'),
    '作成中', '0', ''
  ]);
  setTestInfoValue(ss, '現在ステップ', '0');

  PropertiesService.getScriptProperties().setProperty('ACTIVE_TEST_SS_ID', ss.getId());

  return {
    testSsId: ss.getId(),
    url: ss.getUrl(),
    testName: testName,
    folderId: folder.getId()
  };
}

function listTests(limit) {
  initializeHub();
  syncHubTestList();
  var hubSs = getHubSs();
  setupHubSheets(hubSs);
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var headers = data[0];
  var colStep = headers.indexOf('現在ステップ');
  var colSaved = headers.indexOf('最終保存日時');
  var activeId = getActiveTestSsId();
  var list = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][1]) continue;
    list.push({
      testName: data[i][0],
      testSsId: data[i][1],
      url: data[i][2],
      createdAt: data[i][3],
      status: data[i][4],
      currentStep: colStep >= 0 ? data[i][colStep] : '',
      lastSavedAt: colSaved >= 0 ? data[i][colSaved] : '',
      isActive: data[i][1] === activeId
    });
  }
  list.sort(function(a, b) {
    var da = a.lastSavedAt || a.createdAt || '';
    var db = b.lastSavedAt || b.createdAt || '';
    return db.localeCompare(da);
  });
  if (limit && limit > 0) list = list.slice(0, limit);
  return list;
}

function getRecentTests(limit) {
  return listTests(limit || 20);
}

function getAppBootstrap() {
  try {
    initializeHub();
    var hubSs = getHubSs();
    var tests = listTests(50);
    return {
      ok: true,
      hubSsId: hubSs.getId(),
      hubUrl: hubSs.getUrl(),
      hubName: hubSs.getName(),
      activeTestSsId: getActiveTestSsId(),
      tests: tests
    };
  } catch (e) {
    return {
      ok: false,
      error: e.message || String(e),
      hubSsId: '',
      hubUrl: '',
      hubName: '',
      activeTestSsId: getActiveTestSsId(),
      tests: []
    };
  }
}

function getHubParentFolder_(hubSs) {
  var hubFile = DriveApp.getFileById(hubSs.getId());
  var parents = hubFile.getParents();
  return parents.hasNext() ? parents.next() : null;
}

function buildTestListEntryFromSs(ss) {
  ensureTestInfoKeys(ss);
  return {
    testName: getTestInfoValue(ss, 'テスト名') || ss.getName(),
    testSsId: ss.getId(),
    url: ss.getUrl(),
    createdAt: getTestInfoValue(ss, '作成日時') || Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss'),
    status: getTestInfoValue(ss, 'ステータス') || '作業中',
    currentStep: getTestInfoValue(ss, '現在ステップ') || '0',
    lastSavedAt: getTestInfoValue(ss, '最終保存日時') || ''
  };
}

function discoverTestSpreadsheetsInHubFolder(hubSs) {
  var hubId = hubSs.getId();
  var folder = getHubParentFolder_(hubSs);
  if (!folder) return [];

  var list = [];
  var files = folder.getFilesByType(MimeType.GOOGLE_SHEETS);
  while (files.hasNext()) {
    var file = files.next();
    if (file.getId() === hubId) continue;
    try {
      var ss = SpreadsheetApp.openById(file.getId());
      if (!ss.getSheetByName(SHEET_TEST_INFO)) continue;
      list.push(buildTestListEntryFromSs(ss));
    } catch (err) { /* skip */ }
  }
  return list;
}

function appendHubTestRow(sheet, entry) {
  ensureHubSheetColumns(sheet);
  sheet.appendRow([
    entry.testName,
    entry.testSsId,
    entry.url,
    entry.createdAt,
    entry.status || '作業中',
    entry.currentStep != null ? String(entry.currentStep) : '0',
    entry.lastSavedAt || ''
  ]);
}

function syncHubTestRowFromTestInfo(sheet, rowNum, entry) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colStep = headers.indexOf('現在ステップ');
  var colSaved = headers.indexOf('最終保存日時');
  var colStatus = headers.indexOf('ステータス');
  var colName = headers.indexOf('テスト名');
  if (colName >= 0 && entry.testName) sheet.getRange(rowNum, colName + 1).setValue(entry.testName);
  if (colStep >= 0 && entry.currentStep !== '') sheet.getRange(rowNum, colStep + 1).setValue(String(entry.currentStep));
  if (colSaved >= 0 && entry.lastSavedAt) sheet.getRange(rowNum, colSaved + 1).setValue(entry.lastSavedAt);
  if (colStatus >= 0 && entry.status) sheet.getRange(rowNum, colStatus + 1).setValue(entry.status);
}

function syncHubTestList() {
  var hubSs = getHubSs();
  setupHubSheets(hubSs);
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  ensureHubSheetColumns(sheet);

  var data = sheet.getDataRange().getValues();
  var existing = {};
  for (var i = 1; i < data.length; i++) {
    if (data[i][1]) existing[String(data[i][1])] = i + 1;
  }

  var discovered = discoverTestSpreadsheetsInHubFolder(hubSs);
  discovered.forEach(function(entry) {
    var rowNum = existing[entry.testSsId];
    if (rowNum) {
      syncHubTestRowFromTestInfo(sheet, rowNum, entry);
    } else {
      appendHubTestRow(sheet, entry);
      existing[entry.testSsId] = sheet.getLastRow();
    }
  });

  return discovered.length;
}

function setActiveTest(testSsId) {
  if (!testSsId) throw new Error('テストIDが指定されていません。');
  SpreadsheetApp.openById(testSsId);
  PropertiesService.getScriptProperties().setProperty('ACTIVE_TEST_SS_ID', testSsId);
  return getTestRestoreData(testSsId);
}

function touchTestProgress_(ss, stepNum) {
  var current = parseInt(getTestInfoValue(ss, '現在ステップ'), 10) || 0;
  if (stepNum <= current) return;
  var now = Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss');
  setTestInfoValue(ss, '現在ステップ', String(stepNum));
  setTestInfoValue(ss, '最終保存日時', now);
  updateHubTestProgress(ss.getId(), stepNum, now);
}

function hasGradedResults_(ss) {
  var summary = ss.getSheetByName(SHEET_SUMMARY);
  if (summary && summary.getLastRow() > 1) return true;
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (!sheet || sheet.getLastRow() <= 1) return false;
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var sampleRows = Math.min(sheet.getLastRow() - 1, 10);
  if (sampleRows <= 0) return false;
  var data = sheet.getRange(2, 1, sampleRows, sheet.getLastColumn()).getValues();
  for (var r = 0; r < data.length; r++) {
    var labels = Object.keys(colMap.fields);
    for (var i = 0; i < labels.length; i++) {
      var jIdx = colMap.fields[labels[i]].judgment;
      if (jIdx >= 0 && data[r][jIdx]) return true;
    }
  }
  return false;
}

function inferCompletedSteps_(ss) {
  var list = [0];
  var fields = getAnswerFields(ss);
  if (fields.length && getTestInfoValue(ss, '模範解答画像FileID')) list.push(1);
  if (Object.keys(getPointsMap(ss)).length > 0) list.push(2);
  var results = ss.getSheetByName(SHEET_RESULTS);
  if (results && results.getLastRow() > 1) list.push(3);
  if (getGradingCriteria(ss).length > 0) list.push(4);
  if (hasGradedResults_(ss)) list.push(5);
  var domains = getDomainSettings(ss);
  if (domains.some(function(d) { return d.daiMon || d.hanI || d.noryoku; })) list.push(6);
  var extSheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  if (extSheet && extSheet.getLastRow() > 1) list.push(7);
  if (getIdentityFields(ss).length > 0) list.push(8);
  var maxStep = 0;
  list.forEach(function(s) { if (s > maxStep) maxStep = s; });
  return { list: list, maxStep: maxStep };
}

function getDomainSettingsForUiFromSs(ss) {
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

function getCriteriaGroupedByField_(ss) {
  var rules = getGradingCriteria(ss);
  var grouped = {};
  rules.forEach(function(r) {
    if (!grouped[r.fieldId]) grouped[r.fieldId] = [];
    grouped[r.fieldId].push(r);
  });
  return grouped;
}

function getSummaryDataFromSs(ss) {
  var sheet = ss.getSheetByName(SHEET_SUMMARY);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, 4).getValues().map(function(row) {
    return { category: row[0], item: row[1], value: row[2], note: row[3] };
  });
}

function getExternalScoresFromSs(ss) {
  var sheet = ss.getSheetByName(SHEET_EXTERNAL_SCORES);
  if (!sheet || sheet.getLastRow() <= 1) return [];
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

function getOcrResultPreview_(ss) {
  var fields = getAnswerFields(ss);
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (!sheet || sheet.getLastRow() <= 1) return [];
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  return data.map(function(row) {
    var textMapping = {};
    fields.forEach(function(f) {
      var label = f.displayName || f.id;
      var fm = colMap.fields[label];
      if (fm && fm.text >= 0) textMapping[f.id] = String(row[fm.text] || '');
    });
    return {
      fileId: colMap.fileId >= 0 ? String(row[colMap.fileId] || '') : '',
      fileName: colMap.fileName >= 0 ? String(row[colMap.fileName] || '') : '',
      studentId: colMap.studentId >= 0 ? String(row[colMap.studentId] || '') : '',
      textMapping: textMapping
    };
  });
}

function getBatchRestoreSnapshot_(ss) {
  var folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  var processedIds = Object.keys(getProcessedFileIds(ss));
  var files = [];
  if (folderId) {
    try {
      files = listFolderFiles(folderId);
    } catch (e) { /* folder inaccessible */ }
  }
  return {
    folderId: folderId,
    files: files,
    processedFileIds: processedIds,
    resultPreview: getOcrResultPreview_(ss),
    processedCount: processedIds.length,
    totalFiles: files.length
  };
}

function getTestRestoreData(testSsId) {
  var ss = testSsId ? SpreadsheetApp.openById(testSsId) : getActiveTestSs();
  ensureTestInfoKeys(ss);
  var completed = inferCompletedSteps_(ss);
  var savedStep = parseInt(getTestInfoValue(ss, '現在ステップ'), 10) || 0;
  var resumeStep = savedStep > 0 ? savedStep : completed.maxStep;

  return {
    testSsId: ss.getId(),
    url: ss.getUrl(),
    info: getTestInfoObject(ss),
    fields: getAnswerFields(ss),
    points: getPointsMap(ss),
    identityFields: getIdentityFields(ss),
    domainSettings: getDomainSettingsForUiFromSs(ss),
    criteriaByField: getCriteriaGroupedByField_(ss),
    summary: getSummaryDataFromSs(ss),
    externalScores: getExternalScoresFromSs(ss),
    batchRestore: getBatchRestoreSnapshot_(ss),
    resultRowCount: Math.max(0, (ss.getSheetByName(SHEET_RESULTS).getLastRow() || 1) - 1),
    completedSteps: completed.list,
    currentStep: resumeStep,
    lastSavedAt: getTestInfoValue(ss, '最終保存日時'),
    ocrReplacementsByField: getOcrReplacementsGrouped_(ss),
    activeTestSsId: ss.getId()
  };
}

function getOcrReplacementsGrouped_(ss) {
  ensureOcrReplacementsSheet(ss);
  var all = getOcrReplacementsForSs(ss, null);
  var grouped = {};
  all.forEach(function(r) {
    if (!grouped[r.fieldId]) grouped[r.fieldId] = [];
    grouped[r.fieldId].push(r);
  });
  return grouped;
}

function getTestInfo(testSsId) {
  return getTestRestoreData(testSsId);
}

function updateHubTestProgress(testSsId, stepNum, savedAt) {
  var hubSs = getHubSs();
  var sheet = hubSs.getSheetByName(SHEET_HUB_TEST_LIST);
  ensureHubSheetColumns(sheet);
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var colStep = headers.indexOf('現在ステップ');
  var colSaved = headers.indexOf('最終保存日時');
  for (var i = 1; i < data.length; i++) {
    if (data[i][1] === testSsId) {
      if (colStep >= 0) sheet.getRange(i + 1, colStep + 1).setValue(String(stepNum));
      if (colSaved >= 0) sheet.getRange(i + 1, colSaved + 1).setValue(savedAt);
      if (headers.indexOf('ステータス') >= 0) sheet.getRange(i + 1, headers.indexOf('ステータス') + 1).setValue('作業中');
      return;
    }
  }
  try {
    var ss = SpreadsheetApp.openById(testSsId);
    appendHubTestRow(sheet, buildTestListEntryFromSs(ss));
    var lastRow = sheet.getLastRow();
    if (colStep >= 0) sheet.getRange(lastRow, colStep + 1).setValue(String(stepNum));
    if (colSaved >= 0) sheet.getRange(lastRow, colSaved + 1).setValue(savedAt);
  } catch (e) { /* ignore */ }
}

function saveStepProgress(stepNum, clientPayload) {
  var ss = getActiveTestSs();
  ensureTestInfoKeys(ss);
  var now = Utilities.formatDate(new Date(), 'JST', 'yyyy-MM-dd HH:mm:ss');
  setTestInfoValue(ss, '現在ステップ', String(stepNum));
  setTestInfoValue(ss, '最終保存日時', now);
  updateHubTestProgress(ss.getId(), stepNum, now);

  if (stepNum === 1 && clientPayload && clientPayload.fields) {
    saveAnswerFields(clientPayload.fields);
    if (clientPayload.modelBase64) {
      saveModelAnswerImage(clientPayload.modelBase64, clientPayload.width, clientPayload.height);
    }
  } else if (stepNum === 2 && clientPayload && clientPayload.points) {
    savePoints(clientPayload.points);
  } else if (stepNum === 3 && clientPayload && clientPayload.folderId) {
    setTestInfoValue(ss, '生徒解答フォルダID', clientPayload.folderId);
  } else if (stepNum === 8 && clientPayload && clientPayload.identityFields) {
    saveIdentityFields(clientPayload.identityFields);
  }
  return { step: stepNum, savedAt: now };
}

function checkVisionApiKey() {
  var key = PropertiesService.getScriptProperties().getProperty('VISION_API_KEY');
  if (!key || !String(key).trim()) {
    return { configured: false, message: 'VISION_API_KEY がスクリプトプロパティに未設定です。Apps Script のプロジェクト設定から追加してください。' };
  }
  return { configured: true, message: 'Vision API キーが設定されています。' };
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


// ========== FieldManager.gs ==========

/**
 * 記述欄・本人確認欄・配点の管理
 */

function getAnswerFields(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_ANSWER_FIELDS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var fields = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][0]) continue;
    fields.push({
      id: String(data[i][0]),
      displayName: String(data[i][1] || data[i][0]),
      x: parseInt(data[i][2], 10) || 0,
      y: parseInt(data[i][3], 10) || 0,
      width: parseInt(data[i][4], 10) || 0,
      height: parseInt(data[i][5], 10) || 0,
      order: parseInt(data[i][6], 10) || i
    });
  }
  fields.sort(function(a, b) { return a.order - b.order; });
  return fields;
}

function saveAnswerFields(fields) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_ANSWER_FIELDS);
  sheet.clear();
  sheet.appendRow(['記述欄ID', '表示名', 'x', 'y', 'width', 'height', '表示順']);

  fields.forEach(function(f, idx) {
    sheet.appendRow([
      f.id,
      f.displayName || f.id,
      f.x, f.y, f.width, f.height,
      f.order != null ? f.order : idx + 1
    ]);
  });

  syncPointsSheet(ss, fields);
  rebuildResultsSheetHeaders(ss);
  touchTestProgress_(ss, 1);
  return getAnswerFields(ss);
}

function syncPointsSheet(ss, fields) {
  var sheet = ss.getSheetByName(SHEET_POINTS);
  var existing = {};
  if (sheet.getLastRow() > 1) {
    var data = sheet.getDataRange().getValues();
    for (var i = 1; i < data.length; i++) {
      existing[String(data[i][0])] = data[i][1];
    }
  }
  sheet.clear();
  sheet.appendRow(['記述欄ID', '満点']);
  fields.forEach(function(f) {
    sheet.appendRow([f.id, existing[f.id] != null ? existing[f.id] : 5]);
  });
}

function savePoints(pointsMap) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_POINTS);
  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    var id = String(data[i][0]);
    if (pointsMap[id] != null) {
      sheet.getRange(i + 1, 2).setValue(parseInt(pointsMap[id], 10) || 0);
    }
  }
  touchTestProgress_(ss, 2);
  return getPointsMap(ss);
}

function getPointsMap(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_POINTS);
  var data = sheet.getDataRange().getValues();
  var map = {};
  for (var i = 1; i < data.length; i++) {
    map[String(data[i][0])] = parseInt(data[i][1], 10) || 0;
  }
  return map;
}

function getIdentityFields(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_IDENTITY_FIELDS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var fields = [];
  for (var i = 1; i < data.length; i++) {
    if (!data[i][0]) continue;
    fields.push({
      type: String(data[i][0]),
      x: parseInt(data[i][1], 10) || 0,
      y: parseInt(data[i][2], 10) || 0,
      width: parseInt(data[i][3], 10) || 0,
      height: parseInt(data[i][4], 10) || 0
    });
  }
  return fields;
}

function saveIdentityFields(fields) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_IDENTITY_FIELDS);
  sheet.clear();
  sheet.appendRow(['欄種別', 'x', 'y', 'width', 'height']);
  fields.forEach(function(f) {
    sheet.appendRow([f.type, f.x, f.y, f.width, f.height]);
  });
  touchTestProgress_(ss, 8);
  return getIdentityFields(ss);
}

function fieldsToBoxes(fields) {
  return fields.map(function(f) {
    return { id: f.id, x: f.x, y: f.y, w: f.width, h: f.height };
  });
}


// ========== DriveService.gs ==========

/**
 * Google Drive ファイル操作
 */

var IMAGE_MIME_TYPES = {
  'image/jpeg': true,
  'image/png': true,
  'image/jpg': true,
  'application/pdf': true
};

function listFolderFiles(folderId) {
  if (!folderId) throw new Error('フォルダIDを指定してください。');
  var folder = DriveApp.getFolderById(folderId);
  var files = folder.getFiles();
  var list = [];

  while (files.hasNext()) {
    var file = files.next();
    var mime = file.getMimeType();
    if (!IMAGE_MIME_TYPES[mime]) continue;
    list.push({
      id: file.getId(),
      name: file.getName(),
      mimeType: mime,
      isPdf: mime === 'application/pdf'
    });
  }
  list.sort(function(a, b) { return a.name.localeCompare(b.name, 'ja'); });
  return list;
}

function getDriveFileBase64(fileId) {
  var file = DriveApp.getFileById(fileId);
  var mime = file.getMimeType();
  var blob = file.getBlob();
  var bytes = Utilities.base64Encode(blob.getBytes());
  return {
    fileId: fileId,
    fileName: file.getName(),
    mimeType: mime,
    base64: bytes,
    isPdf: mime === 'application/pdf'
  };
}

function saveWarpedImage(base64Image, originalFileName, studentId) {
  var ss = getActiveTestSs();
  var folder = getOrCreateTestImageFolder(ss);
  var imageBytes = base64Image.split(',')[1];
  var safeId = studentId && !String(studentId).includes('?') ? studentId : 'unknown';
  var fileName = '補正_' + safeId + '_' + (originalFileName || 'image') + '.jpg';
  fileName = fileName.replace(/[^\w\u3040-\u30ff\u4e00-\u9faf.\-]/g, '_').substring(0, 200);
  var file = folder.createFile(Utilities.newBlob(Utilities.base64Decode(imageBytes), 'image/jpeg', fileName));
  return { fileId: file.getId(), fileName: fileName };
}

function getProcessedFileIds(ss) {
  ss = ss || getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return {};
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var fileIdCol = headers.indexOf('ファイルID');
  if (fileIdCol < 0) return {};

  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var map = {};
  data.forEach(function(row) {
    if (row[fileIdCol]) map[String(row[fileIdCol])] = true;
  });
  return map;
}

function isFileAlreadyProcessed(ss, fileId) {
  return !!getProcessedFileIds(ss)[fileId];
}

function cropImageRegion(base64Image, region) {
  // サーバー側クロップは Canvas 不可のため、クライアントで行う。
  // 本人欄用に warped 画像全体を返し、クライアントで crop する方式を IdentityService で使用。
  return base64Image;
}

function getWarpedImageForStudent(sourceFileId) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return null;
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var fileIdCol = headers.indexOf('ファイルID');
  if (fileIdCol < 0) return null;

  var data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][fileIdCol]) === String(sourceFileId)) {
      var folder = getOrCreateTestImageFolder(ss);
      var files = folder.getFiles();
      while (files.hasNext()) {
        var f = files.next();
        if (f.getName().indexOf(sourceFileId) >= 0 || f.getName().indexOf(data[i][0]) >= 0) {
          return getDriveFileBase64(f.getId());
        }
      }
    }
  }
  return getDriveFileBase64(sourceFileId);
}

function getStudentWarpedImagesMeta() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return [];
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var map = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();

  return data.map(function(row, idx) {
    return {
      rowIndex: idx + 2,
      studentId: row[map.studentId] || '',
      fileName: row[map.fileName] || '',
      fileId: row[map.fileId] || '',
      name: row[map.name] || ''
    };
  });
}


// ========== OcrService.gs ==========

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

function getProcessedFileIdList() {
  return Object.keys(getProcessedFileIds());
}

function buildResultRowArray(headers, map, fields, fileMeta, studentId, textMapping) {
  var row = new Array(headers.length).fill('');
  if (map.studentId >= 0) row[map.studentId] = studentId || '';
  if (map.fileName >= 0) row[map.fileName] = fileMeta.fileName || '';
  if (map.fileId >= 0) row[map.fileId] = fileMeta.fileId || '';
  if (map.warpedFileId >= 0) row[map.warpedFileId] = fileMeta.warpedFileId || '';
  fields.forEach(function(f) {
    var label = f.displayName || f.id;
    var fieldMap = map.fields[label];
    if (!fieldMap) return;
    if (fieldMap.text >= 0) row[fieldMap.text] = (textMapping && textMapping[f.id]) || 'なし';
  });
  return row;
}

function ocrStudentPaper(fileMeta, studentId, warpedBase64) {
  try {
    var ss = getActiveTestSs();
    var sourceFileId = fileMeta.id || fileMeta.fileId;
    var sourceFileName = fileMeta.name || fileMeta.fileName || '';
    var fields = getAnswerFields(ss);
    if (fields.length === 0) throw new Error('記述欄が設定されていません。');

    var imageBytes = warpedBase64.split(',')[1];
    var saved = saveWarpedImage(warpedBase64, sourceFileName, studentId);
    var boxes = fieldsToBoxes(fields);
    var visionResult = callVisionAPI(imageBytes);
    var extracted = extractTextFromBoxes(visionResult, boxes);

    var textMapping = {};
    extracted.forEach(function(item) {
      textMapping[item.q_id] = item.student_answer;
    });
    var cleanStudentId = (studentId && !String(studentId).includes('?')) ? String(studentId) : '';

    return {
      success: true,
      studentId: cleanStudentId,
      fileId: sourceFileId,
      fileName: sourceFileName,
      warpedFileId: saved.fileId,
      textMapping: textMapping,
      skipped: false
    };
  } catch (error) {
    return {
      success: false,
      error: error.toString(),
      fileId: fileMeta ? (fileMeta.id || fileMeta.fileId) : ''
    };
  }
}

function flushResultRows(rows) {
  if (!rows || rows.length === 0) return { written: 0 };

  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var fields = getAnswerFields(ss);
  if (sheet.getLastRow() === 0) {
    initResultsSheet(sheet, fields, getDynamicResultExtraColumns(ss));
  }
  ensureWarpedFileIdColumn(sheet);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var map = getResultColumnMap(headers);
  var startRow = sheet.getLastRow() + 1;

  var dataRows = rows.map(function(r) {
    return buildResultRowArray(headers, map, fields, {
      fileName: r.fileName,
      fileId: r.fileId,
      warpedFileId: r.warpedFileId
    }, r.studentId, r.textMapping);
  });

  sheet.getRange(startRow, 1, startRow + dataRows.length - 1, headers.length).setValues(dataRows);
  updateTestStatus('テキスト化中');
  touchTestProgress_(ss, 3);
  return { written: dataRows.length };
}

function processStudentPaper(fileMeta, studentId, warpedBase64, skipIfExists) {
  try {
    var ss = getActiveTestSs();
    var sourceFileId = fileMeta.id || fileMeta.fileId;

    if (skipIfExists !== false && sourceFileId && isFileAlreadyProcessed(ss, sourceFileId)) {
      return { success: true, skipped: true, fileId: sourceFileId };
    }

    var ocrResult = ocrStudentPaper(fileMeta, studentId, warpedBase64);
    if (!ocrResult.success) return ocrResult;

    appendResultRow(ss, {
      fileName: ocrResult.fileName,
      fileId: ocrResult.fileId,
      warpedFileId: ocrResult.warpedFileId
    }, ocrResult.studentId, ocrResult.textMapping);

    return {
      success: true,
      studentId: ocrResult.studentId,
      fileId: ocrResult.fileId,
      textMapping: ocrResult.textMapping,
      skipped: false
    };
  } catch (error) {
    return { success: false, error: error.toString(), fileId: fileMeta ? fileMeta.id : '' };
  }
}

function ensureWarpedFileIdColumn(sheet) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  if (headers.indexOf('補正画像FileID') >= 0) return headers;
  var fileIdIdx = headers.indexOf('ファイルID');
  if (fileIdIdx >= 0) {
    sheet.insertColumnAfter(fileIdIdx + 1);
    sheet.getRange(1, fileIdIdx + 2).setValue('補正画像FileID');
  } else {
    sheet.insertColumnAfter(3);
    sheet.getRange(1, 4).setValue('補正画像FileID');
  }
  return sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
}

function appendResultRow(ss, fileMeta, studentId, textMapping) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var fields = getAnswerFields(ss);
  if (sheet.getLastRow() === 0) {
    initResultsSheet(sheet, fields, getDynamicResultExtraColumns(ss));
  }

  ensureWarpedFileIdColumn(sheet);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var map = getResultColumnMap(headers);
  var row = buildResultRowArray(headers, map, fields, fileMeta, studentId, textMapping);
  sheet.appendRow(row);
}

function getResultRowCount() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  return Math.max(0, sheet.getLastRow() - 1);
}


// ========== GradingService.gs ==========

/**
 * 採点基準・一括採点・考査総括
 */

function getFieldTextColName_(fieldId, fields) {
  var targetField = fields.find(function(f) { return f.id === fieldId; });
  if (!targetField) return null;
  var label = targetField.displayName || targetField.id;
  return label + '_テキスト';
}

function getResultSheetColumnIndices_(headers, textColName) {
  return {
    textCol: headers.indexOf(textColName),
    fileIdCol: headers.indexOf('ファイルID'),
    fileNameCol: headers.indexOf('ファイル名'),
    studentIdCol: headers.indexOf('生徒ID'),
    rowIndexCol: headers.indexOf('行番号')
  };
}

function getFieldAnswerDetails_(ss, fieldId) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return [];

  var fields = getAnswerFields(ss);
  var textColName = getFieldTextColName_(fieldId, fields);
  if (!textColName) return [];

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var cols = getResultSheetColumnIndices_(headers, textColName);
  if (cols.textCol === -1) return [];

  var numRows = lastRow - 1;
  var texts = sheet.getRange(2, cols.textCol + 1, numRows, 1).getValues();
  var fileIds = cols.fileIdCol >= 0 ? sheet.getRange(2, cols.fileIdCol + 1, numRows, 1).getValues() : [];
  var fileNames = cols.fileNameCol >= 0 ? sheet.getRange(2, cols.fileNameCol + 1, numRows, 1).getValues() : [];
  var studentIds = cols.studentIdCol >= 0 ? sheet.getRange(2, cols.studentIdCol + 1, numRows, 1).getValues() : [];

  var details = [];
  for (var i = 0; i < numRows; i++) {
    var answer = String(texts[i][0]).trim();
    if (!answer) answer = 'なし';
    details.push({
      rowIndex: i + 2,
      answer: answer,
      fileId: fileIds.length ? String(fileIds[i][0] || '') : '',
      fileName: fileNames.length ? String(fileNames[i][0] || '') : '',
      studentId: studentIds.length ? String(studentIds[i][0] || '') : ''
    });
  }
  return details;
}

function applyReplacementRules_(text, rules) {
  var result = String(text);
  (rules || []).forEach(function(r) {
    if (!r || !r.search) return;
    if (r.useRegex) {
      try {
        result = result.replace(new RegExp(r.search, 'gi'), r.replace || '');
      } catch (e) { /* invalid regex */ }
    } else {
      var parts = result.split(r.search);
      result = parts.join(r.replace || '');
    }
  });
  result = result.trim();
  return result || 'なし';
}

function getOcrReplacementsForSs(ss, fieldId) {
  ensureOcrReplacementsSheet(ss);
  var sheet = ss.getSheetByName(SHEET_OCR_REPLACEMENTS);
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  var rules = [];
  for (var i = 1; i < data.length; i++) {
    var fid = String(data[i][0] || '').trim();
    if (!fid) continue;
    if (fieldId && fid !== fieldId) continue;
    var search = String(data[i][1] || '');
    if (!search) continue;
    rules.push({
      fieldId: fid,
      search: search,
      replace: String(data[i][2] != null ? data[i][2] : ''),
      useRegex: data[i][3] === true || String(data[i][3]).toUpperCase() === 'TRUE'
    });
  }
  return rules;
}

function getOcrReplacements(fieldId) {
  return getOcrReplacementsForSs(getActiveTestSs(), fieldId);
}

function saveOcrReplacements(fieldId, rules) {
  var ss = getActiveTestSs();
  ensureOcrReplacementsSheet(ss);
  var sheet = ss.getSheetByName(SHEET_OCR_REPLACEMENTS);
  var data = sheet.getDataRange().getValues();
  var kept = data.length > 1 ? [data[0]] : [['記述欄ID', '検索文字列', '置換後', '正規表現']];

  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0] || '').trim() !== fieldId) kept.push(data[i]);
  }
  (rules || []).forEach(function(r) {
    if (!r || !r.search) return;
    kept.push([
      fieldId,
      r.search,
      r.replace != null ? r.replace : '',
      r.useRegex ? true : false
    ]);
  });

  sheet.clearContents();
  if (kept.length) sheet.getRange(1, 1, kept.length, kept[0].length).setValues(kept);
  sheet.setFrozenRows(1);
  return getOcrReplacements(fieldId);
}

function applyTextReplacementsToField(fieldId, rules) {
  var ss = getActiveTestSs();
  if (rules && rules.length) saveOcrReplacements(fieldId, rules);
  else rules = getOcrReplacements(fieldId);
  if (!rules.length) return getUniqueAnswers(fieldId);

  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) return [];

  var fields = getAnswerFields(ss);
  var textColName = getFieldTextColName_(fieldId, fields);
  if (!textColName) return [];

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var textCol = headers.indexOf(textColName);
  if (textCol === -1) return [];

  var numRows = lastRow - 1;
  var texts = sheet.getRange(2, textCol + 1, numRows, 1).getValues();
  for (var i = 0; i < texts.length; i++) {
    texts[i][0] = applyReplacementRules_(texts[i][0], rules);
  }
  sheet.getRange(2, textCol + 1, numRows, 1).setValues(texts);
  return getUniqueAnswers(fieldId);
}

function getUniqueAnswers(fieldId) {
  var ss = getActiveTestSs();
  var details = getFieldAnswerDetails_(ss, fieldId);
  if (!details.length) return [];

  var countMap = {};
  details.forEach(function(row) {
    countMap[row.answer] = (countMap[row.answer] || 0) + 1;
  });

  return Object.keys(countMap).map(function(key) {
    return { answer_text: key, count: countMap[key] };
  }).sort(function(a, b) { return b.count - a.count; });
}

function getOutlierAnswerGroups(fieldId, maxCount) {
  maxCount = maxCount != null ? parseInt(maxCount, 10) : 1;
  if (isNaN(maxCount) || maxCount < 1) maxCount = 1;

  var ss = getActiveTestSs();
  var details = getFieldAnswerDetails_(ss, fieldId);
  var countMap = {};
  details.forEach(function(row) {
    if (!countMap[row.answer]) {
      countMap[row.answer] = { answer_text: row.answer, count: 0, rows: [] };
    }
    countMap[row.answer].count++;
    countMap[row.answer].rows.push({
      rowIndex: row.rowIndex,
      studentId: row.studentId,
      fileName: row.fileName,
      fileId: row.fileId
    });
  });

  return Object.keys(countMap)
    .filter(function(k) { return countMap[k].count <= maxCount; })
    .map(function(k) { return countMap[k]; })
    .sort(function(a, b) { return a.count - b.count || a.answer_text.localeCompare(b.answer_text); });
}

function getAnswerRowsForPattern(fieldId, answerText) {
  var ss = getActiveTestSs();
  var details = getFieldAnswerDetails_(ss, fieldId);
  var target = String(answerText || '').trim() || 'なし';
  return details.filter(function(row) { return row.answer === target; }).map(function(row) {
    return {
      rowIndex: row.rowIndex,
      studentId: row.studentId,
      fileName: row.fileName,
      fileId: row.fileId,
      answer_text: row.answer
    };
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
  touchTestProgress_(ss, 4);
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
  touchTestProgress_(ss, 5);
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


// ========== DomainService.gs ==========

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
  touchTestProgress_(ss, 6);
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

    Object.keys(daiMonGroups).forEach(function(k) {
      var colName = '大問' + k + '_得点';
      var idx = headers.indexOf(colName);
      if (idx >= 0) row[idx] = sumFieldScores(row, daiMonGroups[k]);
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

    // 総計点は記述欄ごとの得点合計＋外部得点（領域列は内訳表示のみで加算しない）
    var subtotal = 0;
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


// ========== ExternalScoreService.gs ==========

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
  touchTestProgress_(getActiveTestSs(), 7);
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


// ========== IdentityService.gs ==========

/**
 * 本人確認欄・ID/氏名照合
 */

function getVerificationData() {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return { rows: [], identityFields: getIdentityFields(ss) };

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();

  var rows = data.map(function(row, idx) {
    return {
      rowIndex: idx + 2,
      studentId: String(row[colMap.studentId] || ''),
      fileName: String(row[colMap.fileName] || ''),
      fileId: String(row[colMap.fileId] || ''),
      name: String(row[colMap.name] || ''),
      warpedFileId: ''
    };
  });

  return {
    rows: rows,
    identityFields: getIdentityFields(ss),
    answerFields: getAnswerFields(ss)
  };
}

function updateStudentIdentity(rowIndex, studentId, name) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);

  if (colMap.studentId >= 0) sheet.getRange(rowIndex, colMap.studentId + 1).setValue(studentId || '');
  if (colMap.name >= 0) sheet.getRange(rowIndex, colMap.name + 1).setValue(name || '');
  return true;
}

function verifyIdentityWithRoster(rosterRows) {
  var ss = getActiveTestSs();
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return [];

  var rosterMap = {};
  (rosterRows || []).forEach(function(r) {
    if (r.studentId) rosterMap[String(r.studentId).trim()] = String(r.name || '').trim();
  });

  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  var results = [];

  for (var i = 0; i < data.length; i++) {
    var row = data[i];
    var sid = String(row[colMap.studentId] || '').trim();
    var name = String(row[colMap.name] || '').trim();
    var rosterName = sid ? (rosterMap[sid] || '') : '';
    var idMatch = !!sid;
    var nameMatch = !rosterName || !name || rosterName === name;
    var status = 'ok';
    if (!sid) status = 'no_id';
    else if (!nameMatch) status = 'name_mismatch';
    else if (rosterName && !name) status = 'name_empty';

    results.push({
      rowIndex: i + 2,
      studentId: sid,
      name: name,
      rosterName: rosterName,
      fileName: String(row[colMap.fileName] || ''),
      fileId: String(row[colMap.fileId] || ''),
      status: status,
      idMatch: idMatch,
      nameMatch: nameMatch
    });
  }
  return results;
}

function parseRosterCsv(csvText) {
  var lines = String(csvText || '').split(/\r?\n/).filter(function(l) { return l.trim(); });
  var rows = [];
  lines.forEach(function(line, idx) {
    var parts = line.split(/[,;\t]/);
    if (parts.length < 2) return;
    if (idx === 0 && (parts[0].indexOf('ID') >= 0 || parts[0].indexOf('生徒') >= 0)) return;
    rows.push({ studentId: parts[0].trim(), name: parts[1].trim() });
  });
  return rows;
}

function getWarpedFileIdFromResults(ss, sourceFileId) {
  var sheet = ss.getSheetByName(SHEET_RESULTS);
  if (sheet.getLastRow() <= 1) return '';
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var colMap = getResultColumnMap(headers);
  if (colMap.fileId < 0 || colMap.warpedFileId < 0) return '';
  var data = sheet.getRange(2, 1, sheet.getLastRow() - 1, sheet.getLastColumn()).getValues();
  for (var i = 0; i < data.length; i++) {
    if (String(data[i][colMap.fileId]) === String(sourceFileId)) {
      return String(data[i][colMap.warpedFileId] || '');
    }
  }
  return '';
}

function cleanupWarpScriptProperties() {
  var props = PropertiesService.getScriptProperties();
  var all = props.getProperties();
  var removed = 0;
  Object.keys(all).forEach(function(key) {
    if (key.indexOf('WARP_') === 0) {
      props.deleteProperty(key);
      removed++;
    }
  });
  SpreadsheetApp.getUi().alert('WARP_* プロパティを ' + removed + ' 件削除しました。');
  return removed;
}

function getWarpedImageBase64(sourceFileId) {
  var ss = getActiveTestSs();
  var warpedId = getWarpedFileIdFromResults(ss, sourceFileId);
  if (warpedId) {
    try {
      return getDriveFileBase64(warpedId);
    } catch (e) { /* fallback */ }
  }
  var folder = getOrCreateTestImageFolder(ss);
  var files = folder.getFiles();
  while (files.hasNext()) {
    var f = files.next();
    if (f.getName().indexOf(String(sourceFileId)) >= 0 && f.getName().indexOf('補正_') === 0) {
      return getDriveFileBase64(f.getId());
    }
  }
  return getDriveFileBase64(sourceFileId);
}

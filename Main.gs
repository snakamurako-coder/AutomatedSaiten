/**
 * エントリーポイント・共通ユーティリティ
 */

function doGet() {
  initializeHub();
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('模範解答ベース自動採点システム')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

function initializeHub() {
  const properties = PropertiesService.getScriptProperties();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) throw new Error('アクティブなスプレッドシートが見つかりません。');
  setupHubSheets(ss);

  let rootFolderId = properties.getProperty('ROOT_IMAGE_FOLDER_ID');
  if (rootFolderId) {
    try {
      DriveApp.getFolderById(rootFolderId);
      return;
    } catch (e) { /* recreate */ }
  }

  const file = DriveApp.getFileById(ss.getId());
  const parents = file.getParents();
  if (!parents.hasNext()) throw new Error('親フォルダの取得に失敗しました。');

  const parentFolder = parents.next();
  const subFolders = parentFolder.getFoldersByName('採点システム画像');
  const rootFolder = subFolders.hasNext() ? subFolders.next() : parentFolder.createFolder('採点システム画像');
  properties.setProperty('ROOT_IMAGE_FOLDER_ID', rootFolder.getId());
}

function setupHubSheets(ss) {
  if (!ss.getSheetByName(SHEET_HUB_TEST_LIST)) {
    const sheet = ss.insertSheet(SHEET_HUB_TEST_LIST);
    sheet.appendRow(['テスト名', 'スプレッドシートID', 'URL', '作成日', 'ステータス']);
    sheet.setFrozenRows(1);
  }
  const sheet1 = ss.getSheetByName('シート1');
  if (sheet1 && ss.getSheets().length > 1 && sheet1.getLastRow() === 0) {
    ss.deleteSheet(sheet1);
  }
}

function getActiveTestSs() {
  const id = PropertiesService.getScriptProperties().getProperty('ACTIVE_TEST_SS_ID');
  if (!id) throw new Error('アクティブなテストが選択されていません。テストを作成または選択してください。');
  return SpreadsheetApp.openById(id);
}

function getActiveTestSsId() {
  return PropertiesService.getScriptProperties().getProperty('ACTIVE_TEST_SS_ID') || '';
}

function getHubSs() {
  return SpreadsheetApp.getActiveSpreadsheet();
}

function getTestImageRootFolder() {
  const properties = PropertiesService.getScriptProperties();
  const rootId = properties.getProperty('ROOT_IMAGE_FOLDER_ID') || (initializeHub(), properties.getProperty('ROOT_IMAGE_FOLDER_ID'));
  return DriveApp.getFolderById(rootId);
}

function getOrCreateTestImageFolder(ss) {
  const folderId = getTestInfoValue(ss, '生徒解答フォルダID');
  if (folderId) {
    try {
      return DriveApp.getFolderById(folderId);
    } catch (e) { /* recreate */ }
  }
  const testName = getTestInfoValue(ss, 'テスト名') || ss.getName();
  const root = getTestImageRootFolder();
  const sub = root.createFolder(testName + '_' + ss.getId().substring(0, 8));
  setTestInfoValue(ss, '生徒解答フォルダID', sub.getId());
  return sub;
}

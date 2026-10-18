/**
 * Bureau of Leisure: self-updating Apps Script.
 *
 * This file is pasted into the sheet's Apps Script project ONCE. Every hour it looks at the GitHub repo
 * (bureauofleisure/pipeline, branch main). If publisher.gs has changed, it replaces the project's "Code"
 * file with the new version, keeps itself and the manifest intact, and re-runs setupTrigger so any new
 * triggers exist. Changes approved in Claude and pushed to GitHub go live here without anyone pasting.
 *
 * One-time setup (see README):
 *   1. Paste this file as "Updater.gs" and the manifest from apps_script/appsscript.json.
 *   2. Turn on the Apps Script API for your Google account: script.google.com/home/usersettings.
 *   3. Run installUpdater() once and authorize.
 * Talks to the Apps Script REST API directly with the script's own OAuth token, so no advanced service
 * needs adding in the editor.
 */

const REPO_RAW = 'https://raw.githubusercontent.com/bureauofleisure/pipeline/main/';
const CODE_FILE = 'publisher.gs';           // in the repo
const CODE_NAME = 'Code';                    // in this Apps Script project

function installUpdater() {
  ScriptApp.getProjectTriggers().forEach(t => { if (t.getHandlerFunction() === 'pullFromGitHub') ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('pullFromGitHub').timeBased().everyHours(1).create();
  PropertiesService.getScriptProperties().deleteProperty('CODE_SHA');
  pullFromGitHub();
}

function pullFromGitHub() {
  const props = PropertiesService.getScriptProperties();
  const res = UrlFetchApp.fetch(REPO_RAW + CODE_FILE + '?t=' + Date.now(), { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) { log_('updater', 'GitHub fetch failed: ' + res.getResponseCode()); return; }
  const source = res.getContentText();
  const sha = Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_1, source));
  if (sha === props.getProperty('CODE_SHA')) return;          // nothing new

  const scriptId = ScriptApp.getScriptId();
  const api = 'https://script.googleapis.com/v1/projects/' + scriptId + '/content';
  const headers = { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() };
  const got = UrlFetchApp.fetch(api, { headers, muteHttpExceptions: true });
  if (got.getResponseCode() !== 200) { log_('updater', 'read project failed: ' + got.getResponseCode() + ' ' + got.getContentText().slice(0, 160)); return; }
  const current = JSON.parse(got.getContentText());
  const keep = current.files.filter(f => f.name !== CODE_NAME);
  const files = keep.concat([{ name: CODE_NAME, type: 'SERVER_JS', source }]);
  const put = UrlFetchApp.fetch(api, { method: 'put', headers, contentType: 'application/json',
                                       payload: JSON.stringify({ files }), muteHttpExceptions: true });
  if (put.getResponseCode() !== 200) { log_('updater', 'write project failed: ' + put.getResponseCode() + ' ' + put.getContentText().slice(0, 160)); return; }
  props.setProperty('CODE_SHA', sha);
  const version = (source.match(/Publisher v([\d.]+)/) || [])[1] || '?';
  log_('updater', 'publisher updated to v' + version);

  // the new code may declare new triggers; let it set them up, on the next tick so the new file is loaded
  ScriptApp.getProjectTriggers().forEach(t => { if (t.getHandlerFunction() === 'afterUpdate_') ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('afterUpdate_').timeBased().after(60 * 1000).create();
}

function afterUpdate_() {
  ScriptApp.getProjectTriggers().forEach(t => { if (t.getHandlerFunction() === 'afterUpdate_') ScriptApp.deleteTrigger(t); });
  try {
    if (typeof setupTrigger === 'function') { setupTrigger(); log_('updater', 'triggers refreshed'); }
    // setupTrigger in the publisher deletes all triggers, so put the updater's own back
    if (!ScriptApp.getProjectTriggers().some(t => t.getHandlerFunction() === 'pullFromGitHub')) {
      ScriptApp.newTrigger('pullFromGitHub').timeBased().everyHours(1).create();
    }
  } catch (e) {
    log_('updater', 'afterUpdate failed: ' + e);
  }
}

function log_(what, msg) {
  const ss = SpreadsheetApp.getActive();
  const log = ss.getSheetByName('Log') || ss.insertSheet('Log');
  log.appendRow([new Date(), what, msg]);
}

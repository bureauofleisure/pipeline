/**
 * Bureau of Leisure: loader.
 *
 * This is the only code that lives permanently in the sheet's Apps Script project. Every function below
 * is a stub: when it runs, it fetches the current publisher.gs from GitHub (bureauofleisure/pipeline, main),
 * caches it for an hour, and runs the real function from that source. A change approved in Claude and
 * pushed to GitHub is live here within the hour. Nothing to paste, nothing to enable.
 *
 * One-time setup: paste this file as Loader.gs, leave Code.gs empty, run setupTrigger once and authorize.
 */

const REPO_RAW = 'https://raw.githubusercontent.com/bureauofleisure/pipeline/main/publisher.gs';
const CACHE_SECONDS = 3600;

// ---- the stubs: one per function a trigger or a person calls
function setupTrigger()  { return run_('setupTrigger'); }
function publishDue()    { return run_('publishDue'); }
function dailyCheck()    { return run_('dailyCheck'); }
function refreshToken()  { return run_('refreshToken'); }
function importBatch()   { return run_('importBatch'); }
function syncStatuses()  { return run_('syncStatuses'); }

/** Force the next run to fetch fresh code (handy right after a change is pushed). */
function refreshCode() {
  CacheService.getScriptCache().remove('publisher_src');
  const src = source_();
  const version = (src.match(/Publisher v([\d.]+)/) || [])[1] || '?';
  log_('loader', 'publisher v' + version + ' loaded (' + src.length + ' chars)');
  return version;
}

function source_() {
  const cache = CacheService.getScriptCache();
  let src = cache.get('publisher_src');
  if (src) return src;
  const res = UrlFetchApp.fetch(REPO_RAW + '?t=' + Date.now(), { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) throw new Error('Could not fetch publisher.gs from GitHub: HTTP ' + res.getResponseCode());
  src = res.getContentText();
  if (src.length < 2000 || src.indexOf('Bureau of Leisure Publisher') < 0) throw new Error('publisher.gs looks wrong; not running it');
  try { cache.put('publisher_src', src, CACHE_SECONDS); } catch (e) { /* over the cache size limit: just run uncached */ }
  return src;
}

function run_(name) {
  const src = source_();
  // evaluate the publisher in a private scope and hand back the one function asked for
  const pick = new Function(src + '\nreturn (typeof ' + name + ' === "function") ? ' + name + ' : null;');
  const fn = pick();
  if (!fn) throw new Error(name + ' is not defined in publisher.gs');
  return fn();
}

function log_(what, msg) {
  const ss = SpreadsheetApp.getActive();
  const log = ss.getSheetByName('Log') || ss.insertSheet('Log');
  log.appendRow([new Date(), what, msg]);
}

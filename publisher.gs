/**
 * Bureau of Leisure Publisher v0.4 (draft, untested until the Meta app exists)
 *
 * Google Apps Script bound to the Bureau of Leisure Index sheet.
 * Runs hourly. Publishes the next APPROVED row whose "Publish at" time has passed,
 * as an Instagram carousel, through Instagram's official content publishing API.
 *
 * One-time setup:
 *   1. Extensions > Apps Script in the Bureau of Leisure Index sheet, paste this file.
 *   2. Project Settings > Script Properties:
 *        IG_USER_ID    the Instagram professional account ID
 *        IG_TOKEN      a long-lived access token with content publish permission
 *        GRAPH_HOST    graph.instagram.com (Instagram Login, recommended, the default)
 *                      or graph.facebook.com (Facebook Login)
 *   3. Run setupTrigger() once and approve the permissions.
 *      This creates the hourly publisher and a weekly token refresh (tokens die after 60 days unrefreshed).
 *   4. Test with a separate test account before pointing it at the real one.
 *
 * Sheet tab "Posts", header row 1. Columns this script uses (by header name):
 *   Status        DRAFT | APPROVED | KILLED | PUBLISHING | POSTED | FAILED
 *   Index No.
 *   Publish at    a date-time cell
 *   Caption
 *   User tags     handles separated by commas, no @ (tagged on slide 1)
 *   Slide links   one public JPEG link per line, in order (2 to 10)
 *   Alt text      one line per slide, same order
 *   Live link     written by this script
 *   Notes         errors are written here
 * Optional columns (v0.3). When Caption is blank, the caption is built from these:
 *   Card text     the index card, one line per line
 *   Place, City, Designer, Designer handle, Venue handle, Photographer, Photographer handle, Source handle
 *   Collaborators handles (max 3) invited as collab authors, so the post can appear on their profile too
 */

const API_VERSION = 'v25.0';
const SHEET_NAME = 'Posts';

function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('publishDue').timeBased().everyHours(1).create();
  ScriptApp.newTrigger('refreshToken').timeBased().everyDays(7).create();
  ScriptApp.newTrigger('dailyCheck').timeBased().everyDays(1).atHour(7).create();
}

/** Where alerts go. Script Property ALERT_EMAIL, else the script owner. */
function notify_(subject, body) {
  const to = PropertiesService.getScriptProperties().getProperty('ALERT_EMAIL') || Session.getEffectiveUser().getEmail();
  MailApp.sendEmail(to, '[Bureau of Leisure] ' + subject, body);
}

/**
 * Daily, 7am:
 *  1. Gives every APPROVED row without a date the next free daily slot (POST_HOUR, default 9).
 *  2. Alerts if fewer than 7 approved posts are queued (review week missed).
 *  3. Alerts if nothing has posted in 26 hours while approved posts are waiting.
 */
function dailyCheck() {
  const props = PropertiesService.getScriptProperties();
  const hour = Number(props.getProperty('POST_HOUR') || 9);
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  const values = sheet.getDataRange().getValues();
  const col = indexHeaders_(values[0]);
  const now = new Date();
  const taken = new Set();
  let lastPosted = 0, queued = 0;
  values.slice(1).forEach(row => {
    const d = row[col['Publish at']];
    if (d instanceof Date) taken.add(d.toDateString());
    if (row[col['Status']] === 'POSTED' && d instanceof Date) lastPosted = Math.max(lastPosted, d.getTime());
  });
  for (let r = 1; r < values.length; r++) {
    if (values[r][col['Status']] !== 'APPROVED') continue;
    let d = values[r][col['Publish at']];
    if (!(d instanceof Date)) {
      d = new Date(now); d.setHours(hour, 0, 0, 0);
      if (d <= now) d.setDate(d.getDate() + 1);
      while (taken.has(d.toDateString())) d.setDate(d.getDate() + 1);
      taken.add(d.toDateString());
      setCell_(sheet, r, col['Publish at'], d);
    }
    if (d > now) queued++;
  }
  if (queued < 7) notify_(`Only ${queued} approved posts queued`, 'Approve next week in the Bureau of Leisure Index sheet, Posts tab.');
  if (queued > 0 && now.getTime() - lastPosted > 26 * 3600 * 1000) notify_('Nothing posted in 26 hours', 'Check FAILED rows and the Notes column.');
}

/**
 * Long-lived Instagram Login tokens last 60 days and die for good if not refreshed.
 * Runs weekly. Token must be at least 24 hours old and still valid.
 * Failure is logged to the "Notes" of a Log tab so it is visible in the weekly review.
 */
function refreshToken() {
  const props = PropertiesService.getScriptProperties();
  const host = props.getProperty('GRAPH_HOST') || 'graph.instagram.com';
  if (host !== 'graph.instagram.com') return;     // Facebook Login page tokens are refreshed differently
  const ss = SpreadsheetApp.getActive();
  const log = ss.getSheetByName('Log') || ss.insertSheet('Log');
  try {
    const body = get_(`https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=${encodeURIComponent(props.getProperty('IG_TOKEN'))}`);
    props.setProperty('IG_TOKEN', body.access_token);
    log.appendRow([new Date(), 'TOKEN REFRESHED', `expires in ${Math.round(body.expires_in / 86400)} days`]);
  } catch (e) {
    log.appendRow([new Date(), 'TOKEN REFRESH FAILED', String(e).slice(0, 500)]);
  }
}

function publishDue() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;           // never two runs at once
  try {
    const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
    const values = sheet.getDataRange().getValues();
    const col = indexHeaders_(values[0]);
    const now = new Date();

    // never more than one post in any 20-hour window, even when catching up after an outage
    let lastPosted = 0;
    values.slice(1).forEach(row => {
      const d = row[col['Publish at']];
      if (row[col['Status']] === 'POSTED' && d instanceof Date) lastPosted = Math.max(lastPosted, d.getTime());
    });
    if (now.getTime() - lastPosted < 20 * 3600 * 1000) return;

    for (let r = 1; r < values.length; r++) {
      const row = values[r];
      if (row[col['Status']] !== 'APPROVED') continue;
      const when = row[col['Publish at']];
      if (!(when instanceof Date) || when > now) continue;

      // every slide must be live on the public host before Instagram is asked to fetch it
      const missing = lines_(row[col['Slide links']]).filter(u => !urlLive_(u));
      if (missing.length) {
        const waited = (now.getTime() - when.getTime()) / 3600000;
        setCell_(sheet, r, col['Notes'], `Waiting for slides to go live (${missing.length} not reachable), ${new Date().toISOString()}`);
        if (waited > 6) notify_('Slides not reachable: ' + row[col['Index No.']], missing.join('\n'));
        continue;                                          // stays APPROVED, retried next hour
      }

      setCell_(sheet, r, col['Status'], 'PUBLISHING');   // claim the row first
      SpreadsheetApp.flush();
      try {
        let permalink, note = 'Posted ';
        try {
          permalink = publishCarousel_(row, col, false);
        } catch (first) {
          // The most common avoidable failure is a bad tag handle. Retry once without tags.
          if (!String(row[col['User tags']] || '').trim()) throw first;
          permalink = publishCarousel_(row, col, true);
          note = 'Posted WITHOUT TAGS (tag error: ' + String(first).slice(0, 200) + ') ';
          notify_('Posted without tags: ' + row[col['Index No.']], note);
        }
        setCell_(sheet, r, col['Status'], 'POSTED');
        setCell_(sheet, r, col['Live link'], permalink);
        setCell_(sheet, r, col['Notes'], note + new Date().toISOString());
      } catch (e) {
        setCell_(sheet, r, col['Status'], 'FAILED');     // never retried silently
        setCell_(sheet, r, col['Notes'], String(e).slice(0, 500));
        notify_('FAILED: ' + row[col['Index No.']], String(e).slice(0, 1000) + '\n\nFix the row and set Status back to APPROVED.');
      }
      break;                                             // one post per run, at most
    }
  } finally {
    lock.releaseLock();
  }
}

function publishCarousel_(row, col, skipTags) {
  const props = PropertiesService.getScriptProperties();
  const igId = props.getProperty('IG_USER_ID');
  const token = props.getProperty('IG_TOKEN');
  const host = props.getProperty('GRAPH_HOST') || 'graph.instagram.com';
  const base = `https://${host}/${API_VERSION}`;

  const slides = lines_(row[col['Slide links']]);
  const alts = lines_(row[col['Alt text']]);
  if (slides.length < 2 || slides.length > 10) throw new Error(`Need 2 to 10 slides, found ${slides.length}`);

  const tags = skipTags ? [] : String(row[col['User tags']] || '')
    .split(',').map(s => s.trim().replace(/^@/, '')).filter(Boolean)
    .slice(0, 5)
    .map((username, i) => ({ username, x: 0.15 + 0.15 * i, y: 0.9 }));

  // 1. one container per slide
  const children = slides.map((url, i) => {
    const p = { image_url: url, is_carousel_item: 'true', access_token: token };
    if (alts[i]) p.alt_text = alts[i].slice(0, 1000);
    if (i === 0 && tags.length) p.user_tags = JSON.stringify(tags);
    return post_(`${base}/${igId}/media`, p).id;
  });
  children.forEach(id => waitFinished_(base, id, token));

  // 2. the carousel container
  const collabs = cell_(row, col, 'Collaborators').split(',').map(h => h.trim().replace(/^@/, '')).filter(Boolean).slice(0, 3);
  const carouselParams = {
    media_type: 'CAROUSEL',
    children: children.join(','),
    caption: buildCaption_(row, col),
    access_token: token,
  };
  if (collabs.length && !skipTags) carouselParams.collaborators = JSON.stringify(collabs);
  const carousel = post_(`${base}/${igId}/media`, carouselParams).id;
  waitFinished_(base, carousel, token);

  // 3. publish, then fetch the live link
  const mediaId = post_(`${base}/${igId}/media_publish`, { creation_id: carousel, access_token: token }).id;
  const info = get_(`${base}/${mediaId}?fields=permalink&access_token=${encodeURIComponent(token)}`);
  return info.permalink || mediaId;
}

/** Caption = Caption column if filled, else card text + a credits block built from the optional columns. */
function buildCaption_(row, col) {
  const manual = cell_(row, col, 'Caption');
  if (manual) return manual.slice(0, 2200);
  const at = h => (h ? ' @' + String(h).replace(/^@/, '') : '');
  const place = [cell_(row, col, 'Place'), cell_(row, col, 'City')].filter(Boolean).join(', ');
  const lines = [];
  if (place) lines.push(place + at(cell_(row, col, 'Venue handle')));
  if (cell_(row, col, 'Designer')) lines.push('Design: ' + cell_(row, col, 'Designer') + at(cell_(row, col, 'Designer handle')));
  if (cell_(row, col, 'Photographer')) lines.push('Photo: ' + cell_(row, col, 'Photographer') + at(cell_(row, col, 'Photographer handle')));
  else lines.push('Photo credit on request. DM us.');
  if (cell_(row, col, 'Source handle')) lines.push('Via' + at(cell_(row, col, 'Source handle')));
  return [cell_(row, col, 'Card text'), lines.join('\n')].filter(Boolean).join('\n.\n').slice(0, 2200);
}

function cell_(row, col, name) {
  return name in col ? String(row[col[name]] || '').trim() : '';
}

function waitFinished_(base, id, token) {
  for (let i = 0; i < 20; i++) {
    const s = get_(`${base}/${id}?fields=status_code&access_token=${encodeURIComponent(token)}`);
    if (s.status_code === 'FINISHED') return;
    if (s.status_code === 'ERROR' || s.status_code === 'EXPIRED') throw new Error(`Container ${id}: ${s.status_code}`);
    Utilities.sleep(3000);
  }
  throw new Error(`Container ${id} not ready after 60 seconds`);
}

function urlLive_(url) {
  try {
    const res = UrlFetchApp.fetch(url, { method: 'get', muteHttpExceptions: true, followRedirects: true,
                                         headers: { Range: 'bytes=0-0' } });
    const code = res.getResponseCode();
    return code === 200 || code === 206;
  } catch (e) {
    return false;
  }
}

function post_(url, payload) {
  const res = UrlFetchApp.fetch(url, { method: 'post', payload, muteHttpExceptions: true });
  const body = JSON.parse(res.getContentText());
  if (res.getResponseCode() >= 300 || body.error) throw new Error(JSON.stringify(body.error || body));
  return body;
}

function get_(url) {
  const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  const body = JSON.parse(res.getContentText());
  if (res.getResponseCode() >= 300 || body.error) throw new Error(JSON.stringify(body.error || body));
  return body;
}

function indexHeaders_(header) {
  const col = {};
  header.forEach((h, i) => (col[String(h).trim()] = i));
  ['Status', 'Publish at', 'Caption', 'User tags', 'Slide links', 'Alt text', 'Live link', 'Notes']
    .forEach(h => { if (!(h in col)) throw new Error(`Missing column: ${h}`); });
  return col;
}

function lines_(v) {
  return String(v || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
}

function setCell_(sheet, r, c, value) {
  sheet.getRange(r + 1, c + 1).setValue(value);
}

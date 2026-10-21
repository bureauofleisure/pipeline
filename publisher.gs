/**
 * Bureau of Leisure Publisher v0.10 (Meta app live; MODE gates public posting, default DRYRUN)
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
 *   4. Nothing else. The first hourly run verifies the token and dry-runs one carousel (built on
 *      Instagram's side, never published) and emails the result. Publishing stays off until that passes.
 *
 * MODE (Script Property). Nothing posts publicly unless MODE is exactly LIVE.
 *   DRYRUN (default, also when MODE is missing): at each 9am slot the due post is built on Instagram's side
 *                 with its tags, checked, and NOT published. Row becomes DRY RUN with a note. One a day,
 *                 so the whole daily rhythm, token refresh and alerts run for real with zero exposure.
 *   LIVE          publishes. On the first LIVE run every DRY RUN row goes back to APPROVED with a fresh slot.
 *
 * v0.9 safety rails:
 *   PAUSE         add a tab named PAUSE to the sheet and nothing publishes; delete it to resume.
 *   Tags          photographer, designer/studio and venue handles are tagged on slide 1 automatically
 *                 (User tags column overrides). A bad or private handle never sinks a post: it retries
 *                 without tags, posts, and emails you which post went out untagged.
 *   Quota         checks Instagram's daily publishing limit before every post.
 *   Token         refreshed weekly; a failed refresh emails you at once, and the daily check warns if the
 *                 token has gone 14 days without a successful refresh (it dies at 60).
 *   Weekly line   Monday 7am email: what went live last week, what failed, what is queued.
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
  ScriptApp.newTrigger('importBatch').timeBased().everyHours(6).create();
  ScriptApp.newTrigger('syncStatuses').timeBased().everyHours(6).create();
}

/** Where alerts go. Script Property ALERT_EMAIL, else the script owner. */
function notify_(subject, body) {
  const props = PropertiesService.getScriptProperties();
  const to = props.getProperty('ALERT_EMAIL') || Session.getEffectiveUser().getEmail();
  const review = props.getProperty('REVIEW_URL');
  const foot = review ? '\n\nReview desk: ' + review : '';
  MailApp.sendEmail(to, '[Bureau of Leisure] ' + subject, body + foot);
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
  if (queued > 0 && now.getTime() - lastPosted > 26 * 3600 * 1000 && !paused_() && live_()) notify_('Nothing posted in 26 hours', 'Check FAILED rows and the Notes column.');

  // token health: refreshed weekly; warn if two weekly refreshes in a row have failed
  const refreshed = Number(props.getProperty('TOKEN_REFRESHED_AT') || props.getProperty('TOKEN_SET_AT') || 0);
  if (!refreshed) props.setProperty('TOKEN_SET_AT', String(Date.now()));
  else if (now.getTime() - refreshed > 14 * 86400000) {
    notify_('Instagram token not refreshed in ' + Math.round((now.getTime() - refreshed) / 86400000) + ' days',
      'It dies 60 days after the last good refresh. Check the Log tab for TOKEN REFRESH FAILED.');
  }

  if (now.getDay() === 1) weeklySummary_(values, col, now);
}

/** Monday: one short email of what went live, what failed and what is queued. */
function weeklySummary_(values, col, now) {
  const weekAgo = now.getTime() - 7 * 86400000;
  const live = [], failed = [], dry = [];
  let queued = 0;
  values.slice(1).forEach(row => {
    const st = row[col['Status']], d = row[col['Publish at']], name = row[col['Index No.']] + ' ' + (cellByName_(row, col, 'Place') || '');
    if (st === 'POSTED' && d instanceof Date && d.getTime() > weekAgo) live.push(name.trim() + '  ' + (row[col['Live link']] || ''));
    if (st === 'FAILED') failed.push(name.trim());
    if (st === 'DRY RUN') dry.push(name.trim() + '  ' + String(row[col['Notes']] || '').slice(0, 60));
    if (st === 'APPROVED') queued++;
  });
  const body = [`Went live (${live.length}):`, ...(live.length ? live : ['none']), '',
                `Failed (${failed.length}):`, ...(failed.length ? failed : ['none']), '',
                `Dry runs (${dry.length}):`, ...(dry.length ? dry : ['none']), '',
                `Approved and queued: ${queued}`, live_() ? 'MODE: LIVE' : 'MODE: DRYRUN (nothing posts publicly)', paused_() ? '\nPUBLISHING IS PAUSED (PAUSE tab exists).' : ''].join('\n');
  notify_(`Week: ${live.length} live, ${failed.length} failed, ${queued} queued`, body);
}

function cellByName_(row, col, name) { return name in col ? String(row[col[name]] || '').trim() : ''; }

/** A tab named PAUSE stops all publishing. */
function paused_() { return !!SpreadsheetApp.getActive().getSheetByName('PAUSE'); }

/** Public posting only when MODE is exactly LIVE. Anything else, including unset, is a dry run. */
function live_() { return PropertiesService.getScriptProperties().getProperty('MODE') === 'LIVE'; }

/**
 * First-run check, and again whenever the token changes. Confirms the token belongs to IG_USER_ID, then builds
 * one real carousel on Instagram's side WITHOUT publishing it (unpublished containers expire on their own).
 * Emails the result. Returns true when publishing is safe.
 */
function verifyConnection_() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('IG_TOKEN') || '';
  const stamp = Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, token)).slice(0, 12);
  if (props.getProperty('IG_VERIFIED_FOR') === stamp) return true;
  const tries = Number(props.getProperty('IG_VERIFY_TRIES_' + stamp) || 0);
  if (tries >= 3) return false;                      // already emailed three failures for this token; wait for a new one
  props.setProperty('IG_VERIFY_TRIES_' + stamp, String(tries + 1));

  const host = props.getProperty('GRAPH_HOST') || 'graph.instagram.com';
  const base = `https://${host}/${API_VERSION}`;
  const igId = props.getProperty('IG_USER_ID');
  try {
    const me = get_(`${base}/me?fields=user_id,username&access_token=${encodeURIComponent(token)}`);
    if (String(me.user_id) !== String(igId) && String(me.id) !== String(igId)) {
      throw new Error(`Token belongs to @${me.username} (${me.user_id || me.id}), but IG_USER_ID is ${igId}`);
    }
    let dry = 'no row with live slides to dry-run yet; account and token check passed';
    const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
    const values = sheet.getDataRange().getValues();
    const col = indexHeaders_(values[0]);
    for (let r = 1; r < values.length; r++) {
      const st = values[r][col['Status']];
      if (st !== 'APPROVED' && st !== 'DRAFT') continue;
      const slides = lines_(values[r][col['Slide links']]);
      if (slides.length < 2 || !slides.every(urlLive_)) continue;
      const carousel = publishCarousel_(values[r], col, false, true);
      dry = `built carousel ${carousel} for ${values[r][col['Index No.']]} (${slides.length} slides, tags included) and did NOT publish it`;
      break;
    }
    props.setProperty('IG_VERIFIED_FOR', stamp);
    props.setProperty('IG_VERIFIED_AT', String(Date.now()));
    log_('verify', `@${me.username} ok; ${dry}`);
    notify_('Instagram connection verified', `Account: @${me.username}\nDry run: ${dry}\n\n` + (live_() ? 'MODE is LIVE: publishing is on. ' : 'MODE is DRYRUN: nothing will post publicly. At each slot the due post is built and checked, not published. ') +
      'Approved posts are handled at ' + (props.getProperty('POST_HOUR') || 9) + ':00 New York time, one a day. Any approved post whose date has already passed is moved to the next free morning slot.');
    return true;
  } catch (e) {
    log_('verify', 'FAILED: ' + String(e).slice(0, 400));
    notify_('Instagram connection check failed', String(e).slice(0, 1000) + '\n\nNothing will publish until this passes. ' +
      'It retries hourly, three times per token.');
    return false;
  }
}

function log_(what, msg) {
  const ss = SpreadsheetApp.getActive();
  const log = ss.getSheetByName('Log') || ss.insertSheet('Log');
  log.appendRow([new Date(), what, msg]);
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
    props.setProperty('TOKEN_REFRESHED_AT', String(Date.now()));
    log.appendRow([new Date(), 'TOKEN REFRESHED', `expires in ${Math.round(body.expires_in / 86400)} days`]);
  } catch (e) {
    log.appendRow([new Date(), 'TOKEN REFRESH FAILED', String(e).slice(0, 500)]);
    // a token under 24 hours old cannot be refreshed yet; that is expected, not a fault
    if (!/24 hours|too soon|not.*eligible/i.test(String(e))) {
      notify_('Instagram token refresh failed', String(e).slice(0, 800) +
        '\n\nThe token still works until it expires (60 days from the last good refresh). ' +
        'If this repeats next week, generate a new token in the Meta app dashboard (Flow Publisher > Use cases > ' +
        'API setup with Instagram login > Generate token) and paste it into Script Property IG_TOKEN.');
    }
  }
}

function publishDue() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;           // never two runs at once
  try {
    if (paused_()) return;                       // a PAUSE tab stops everything
    if (!verifyConnection_()) return;            // token and account proven before anything goes public
    const verifiedAt = Number(PropertiesService.getScriptProperties().getProperty('IG_VERIFIED_AT') || 0);
    if (Date.now() - verifiedAt < 50 * 60000) return;   // the verifying run never publishes
    const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
    const values = sheet.getDataRange().getValues();
    const col = indexHeaders_(values[0]);
    const now = new Date();

    const props = PropertiesService.getScriptProperties();
    if (live_() && !props.getProperty('WENT_LIVE_AT')) {
      // first LIVE run: everything that passed a dry run returns to the queue with a fresh 9am slot
      for (let r = 1; r < values.length; r++) {
        if (values[r][col['Status']] !== 'DRY RUN') continue;
        setCell_(sheet, r, col['Status'], 'APPROVED'); setCell_(sheet, r, col['Publish at'], '');
      }
      props.setProperty('WENT_LIVE_AT', String(Date.now()));
      log_('mode', 'LIVE: dry-run rows returned to APPROVED');
      return;                                    // dailyCheck assigns their slots; first post at the next 9am
    }
    if (!live_() && Date.now() - Number(props.getProperty('LAST_DRYRUN_AT') || 0) < 20 * 3600 * 1000) return;

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
      // more than 6 hours late (outage, pause, or approved with an old date): never fire at a random hour.
      // Clear the date; the 7am dailyCheck gives it the next free 9am slot.
      if (now.getTime() - when.getTime() > 6 * 3600000) {
        setCell_(sheet, r, col['Publish at'], '');
        setCell_(sheet, r, col['Notes'], 'Missed its slot; rescheduled to the next free morning slot ' + now.toISOString());
        continue;
      }

      // every slide must be live on the public host before Instagram is asked to fetch it
      const missing = lines_(row[col['Slide links']]).filter(u => !urlLive_(u));
      if (missing.length) {
        const waited = (now.getTime() - when.getTime()) / 3600000;
        setCell_(sheet, r, col['Notes'], `Waiting for slides to go live (${missing.length} not reachable), ${new Date().toISOString()}`);
        if (waited > 6) notify_('Slides not reachable: ' + row[col['Index No.']], missing.join('\n'));
        continue;                                          // stays APPROVED, retried next hour
      }

      if (!live_()) {
        try {
          const c = publishCarousel_(row, col, false, true);
          setCell_(sheet, r, col['Status'], 'DRY RUN');
          setCell_(sheet, r, col['Notes'], `DRY RUN passed ${new Date().toISOString()}: built ${c} with tags [${tagsFor_(row, col).join(', ')}], not published`);
          log_('dryrun', `${row[col['Index No.']]} ok`);
        } catch (e) {
          setCell_(sheet, r, col['Status'], 'DRY RUN');
          setCell_(sheet, r, col['Notes'], 'DRY RUN FAILED (would have failed live): ' + String(e).slice(0, 400));
          notify_('Dry run failed: ' + row[col['Index No.']], String(e).slice(0, 1000));
        }
        props.setProperty('LAST_DRYRUN_AT', String(Date.now()));
        break;
      }
      if (quotaFull_()) { setCell_(sheet, r, col['Notes'], 'Waiting: Instagram daily publishing limit reached ' + new Date().toISOString()); break; }

      setCell_(sheet, r, col['Status'], 'PUBLISHING');   // claim the row first
      SpreadsheetApp.flush();
      try {
        let permalink, note = 'Posted ';
        try {
          permalink = publishCarousel_(row, col, false);
        } catch (first) {
          // The most common avoidable failure is a bad tag handle. Retry once without tags.
          if (!tagsFor_(row, col).length) throw first;
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

/** Instagram's rolling 24-hour publishing limit. Never blocks on an error reading it. */
function quotaFull_() {
  try {
    const props = PropertiesService.getScriptProperties();
    const base = `https://${props.getProperty('GRAPH_HOST') || 'graph.instagram.com'}/${API_VERSION}`;
    const q = get_(`${base}/${props.getProperty('IG_USER_ID')}/content_publishing_limit?fields=quota_usage,config&access_token=${encodeURIComponent(props.getProperty('IG_TOKEN'))}`);
    const d = (q.data || [])[0] || {};
    return d.config && d.quota_usage >= d.config.quota_total;
  } catch (e) { return false; }
}

/** Handles to tag on slide 1: the User tags column if filled, else photographer, designer/studio and venue. */
function tagsFor_(row, col) {
  const manual = cellByName_(row, col, 'User tags');
  const raw = manual ? manual.split(',') :
    ['Photographer handle', 'Designer handle', 'Venue handle'].map(h => cellByName_(row, col, h));
  const seen = new Set();
  return raw.map(s => String(s).trim().replace(/^@/, '').toLowerCase()).filter(h => h && !seen.has(h) && seen.add(h)).slice(0, 5);
}

function publishCarousel_(row, col, skipTags, dryRun) {
  const props = PropertiesService.getScriptProperties();
  const igId = props.getProperty('IG_USER_ID');
  const token = props.getProperty('IG_TOKEN');
  const host = props.getProperty('GRAPH_HOST') || 'graph.instagram.com';
  const base = `https://${host}/${API_VERSION}`;

  const slides = lines_(row[col['Slide links']]);
  const alts = lines_(row[col['Alt text']]);
  if (slides.length < 2 || slides.length > 10) throw new Error(`Need 2 to 10 slides, found ${slides.length}`);

  const tags = skipTags ? [] : tagsFor_(row, col)
    .map((username, i) => ({ username, x: 0.2 + 0.2 * i, y: 0.88 }));

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
  if (dryRun) return carousel;

  // 3. publish, then fetch the live link
  const mediaId = post_(`${base}/${igId}/media_publish`, { creation_id: carousel, access_token: token }).id;
  const info = get_(`${base}/${mediaId}?fields=permalink&access_token=${encodeURIComponent(token)}`);
  return info.permalink || mediaId;
}

/** Caption = Caption column if filled, else card text + a credits block built from the optional columns. */
function buildCaption_(row, col) {
  const manual = cell_(row, col, 'Caption');
  if (manual) return manual.slice(0, 2200);
  const f = {};
  ['Place', 'City', 'Venue handle', 'Designer', 'Designer handle', 'Photographer', 'Photographer handle', 'Source handle', 'Card text']
    .forEach(h => f[h] = cell_(row, col, h));
  return captionFrom_(f);
}

/** The caption exactly as it will post: the index card, then place, design and photo credits. */
function captionFrom_(f) {
  const at = h => (h ? ' @' + String(h).replace(/^@/, '') : '');
  const place = [f['Place'], f['City']].filter(Boolean).join(', ');
  const lines = [];
  if (place) lines.push(place + at(f['Venue handle']));
  if (f['Designer']) lines.push('Design: ' + f['Designer'] + at(f['Designer handle']));
  if (f['Photographer']) lines.push('Photo: ' + f['Photographer'] + at(f['Photographer handle']));
  if (f['Source handle']) lines.push('Via' + at(f['Source handle']));
  const parts = [f['Card text'], lines.join('\n')];
  if (!f['Photographer']) parts.push('DM for credit.');     // the very last line, only when no name exists
  return parts.filter(Boolean).join('\n.\n').slice(0, 2200);
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


/**
 * importBatch, every 6 hours (v0.5):
 * Reads the build manifest from GitHub Pages and fills the Posts tab.
 *   New index  -> new row, Status DRAFT, every column filled from the manifest.
 *   Known index, still DRAFT -> slide links, alt text, card and credits refreshed (a rebuild changed them).
 *   Known index, any other status -> left alone. A person's decision is never overwritten.
 *   Built posts that fail (BENCHED / ERROR) -> appended to the Candidates tab with the reason, once.
 * Script Property MANIFEST_URL overrides the default.
 */
function importBatch() {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('MANIFEST_URL') || 'https://bureauofleisure.github.io/pipeline/manifest.json';
  let manifest;
  try {
    const res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) throw new Error('HTTP ' + res.getResponseCode());
    manifest = JSON.parse(res.getContentText());
  } catch (e) {
    log_('importBatch', 'manifest unreachable: ' + e);
    const last = Number(props.getProperty('MANIFEST_FAIL_SINCE') || 0);
    if (!last) props.setProperty('MANIFEST_FAIL_SINCE', String(Date.now()));
    else if (Date.now() - last > 24 * 3600000) notify_('Manifest unreachable for a day', url + '\n' + e);
    return;
  }
  props.deleteProperty('MANIFEST_FAIL_SINCE');

  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEET_NAME);
  const values = sheet.getDataRange().getValues();
  const col = indexHeaders_(values[0]);
  // Index No. must stay text ("0009", never 9): format the column, and normalise what is already there
  sheet.getRange(2, col['Index No.'] + 1, Math.max(sheet.getMaxRows() - 1, 1), 1).setNumberFormat('@');
  const norm = v => { const t = String(v || '').trim(); return /^\d+$/.test(t) ? t.padStart(4, '0') : t; };
  const rowOf = {};
  for (let r = 1; r < values.length; r++) {
    const idx = norm(values[r][col['Index No.']]);
    if (!idx) continue;
    if (idx !== String(values[r][col['Index No.']])) sheet.getRange(r + 1, col['Index No.'] + 1).setValue(idx);
    rowOf[idx] = r;
  }

  const cands = ss.getSheetByName('Candidates') || ss.insertSheet('Candidates');
  const seen = new Set(cands.getDataRange().getValues().map(r => String(r[0]).trim()));

  let added = 0, refreshed = 0, benched = 0;
  (manifest.posts || []).forEach(p => {
    const idx = String(p.index).trim();
    if (p.status !== 'BUILT') {
      if (!seen.has(idx)) {
        cands.appendRow([idx, p.place || '', p.status, (p.notes || []).join('; '), new Date()]);
        seen.add(idx); benched++;
      }
      return;
    }
    const m = p.meta || {};
    const fields = {
      'Index No.': idx, 'Place': p.place || '', 'Neighborhood': m.neighborhood || '', 'City': m.city || '',
      'Category': m.category || '', 'Card text': p.caption_card || '', 'Designer': m.designer || '',
      'Designer handle': m.designer_handle || '', 'Venue handle': m.venue_handle || '',
      'Photographer': m.credit || '', 'Photographer handle': m.photographer_handle || '',
      'Source handle': m.source_handle || '', 'Cover slide': p.cover_source_photo || '',
      'Slide links': (p.slides || []).join('\n'),
      'Alt text': Array.isArray(p.alt_text) ? p.alt_text.join('\n') : String(p.alt_text || ''),
    };
    fields['Caption'] = captionFrom_(fields);   // visible and editable before approval; what posts is what is in the cell
    if (idx in rowOf) {
      const r = rowOf[idx];
      const st = String(values[r][col['Status']]).trim();
      if (st !== 'DRAFT' && st !== 'EDITS') return;   // a decision was made: leave it
      // still open: everything the build knows is refreshed, so a rebuild or a corrected card lands here
      Object.keys(fields).forEach(h => { if (h in col && h !== 'Caption') sheet.getRange(r + 1, col[h] + 1).setValue(fields[h]); });
      if (st === 'DRAFT' || !cell_(values[r], col, 'Caption')) sheet.getRange(r + 1, col['Caption'] + 1).setValue(fields['Caption']);
      if (st === 'EDITS') {
        // the edit request carries a timestamp; a build dated after it means the edit was applied
        const m = String(values[r][col['Notes']] || '').match(/EDITS (\d{4}-\d{2}-\d{2} \d{2}:\d{2})/);
        const built = String(p.built || '');
        const edited = p.edited_at ? String(p.edited_at) : '';
        if (m && edited && edited > m[1].replace(' ', 'T')) {
          sheet.getRange(r + 1, col['Status'] + 1).setValue('DRAFT');
          sheet.getRange(r + 1, col['Notes'] + 1).setValue(('Edited, please re-review.\n' + String(values[r][col['Notes']] || '')).slice(0, 5000));
          sheet.getRange(r + 1, col['Caption'] + 1).setValue(fields['Caption']);
        }
      }
      refreshed++;
      return;
    }
    const row = new Array(values[0].length).fill('');
    Object.keys(fields).forEach(h => { if (h in col) row[col[h]] = fields[h]; });
    row[col['Status']] = 'DRAFT';
    row[col['Index No.']] = idx;   // text, see the column format above
    if ('Notes' in col) row[col['Notes']] = (m.bring ? 'BRING idea: ' + m.bring + '\n' : '') +
                                            (m.reminds ? 'Reminds: ' + m.reminds + '\n' : '') +
                                            (m.source_url ? 'Source: ' + m.source_url : '');
    sheet.appendRow(row);
    sheet.getRange(sheet.getLastRow(), col['Index No.'] + 1).setNumberFormat('@').setValue(idx);
    added++;
  });
  log_('importBatch', `${added} new, ${refreshed} refreshed, ${benched} benched`);
  if (added) notify_(`${added} new posts to review`, `${added} new DRAFT rows in the Posts tab. Approve by setting Status to APPROVED.`);
}

// log_ lives in Loader.gs, the permanent file in the sheet's project


/**
 * syncStatuses, every 6 hours (v0.6):
 * Writes every row's Status, Publish at and Live link to discovery/statuses.json in the GitHub repo, so the
 * build side knows what was POSTED (keep the originals forever) and what was KILLED (the janitor removes
 * the originals after 60 days). Needs Script Property GH_TOKEN: a fine-grained GitHub token for
 * bureauofleisure/pipeline with Contents: read and write. Without it this does nothing and logs once a day.
 */
function syncStatuses() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GH_TOKEN');
  if (!token) {
    const last = Number(props.getProperty('GH_TOKEN_WARNED') || 0);
    if (Date.now() - last > 24 * 3600000) { log_('syncStatuses', 'GH_TOKEN not set; statuses not synced'); props.setProperty('GH_TOKEN_WARNED', String(Date.now())); }
    return;
  }
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  const values = sheet.getDataRange().getValues();
  const col = indexHeaders_(values[0]);
  const rows = {};
  for (let r = 1; r < values.length; r++) {
    let idx = String(values[r][col['Index No.']] || '').trim();
    if (/^\d+$/.test(idx)) idx = idx.padStart(4, '0');
    if (!idx) continue;
    const when = values[r][col['Publish at']];
    rows[idx] = {
      status: String(values[r][col['Status']] || '').trim(),
      publish_at: when instanceof Date ? when.toISOString() : String(when || ''),
      live_link: String(values[r][col['Live link']] || ''),
      notes: String(values[r][col['Notes']] || '').slice(0, 2000),
      caption: String(values[r][col['Caption']] || '').slice(0, 2200),
      updated: new Date().toISOString(),
    };
  }
  const body = JSON.stringify({ synced: new Date().toISOString(), rows }, null, 1);
  const api = 'https://api.github.com/repos/bureauofleisure/pipeline/contents/discovery/statuses.json';
  const headers = { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };
  let sha = null;
  const cur = UrlFetchApp.fetch(api, { headers, muteHttpExceptions: true });
  if (cur.getResponseCode() === 200) {
    const j = JSON.parse(cur.getContentText());
    sha = j.sha;
    const existing = Utilities.newBlob(Utilities.base64Decode(j.content.replace(/\n/g, ''))).getDataAsString();
    try {
      if (JSON.stringify(JSON.parse(existing).rows) === JSON.stringify(rows)) return;   // nothing changed
    } catch (e) { /* rewrite */ }
  }
  const payload = { message: 'Statuses from the sheet', content: Utilities.base64Encode(body, Utilities.Charset.UTF_8) };
  if (sha) payload.sha = sha;
  const res = UrlFetchApp.fetch(api, { method: 'put', headers, contentType: 'application/json', payload: JSON.stringify(payload), muteHttpExceptions: true });
  if (res.getResponseCode() >= 300) {
    log_('syncStatuses', 'GitHub write failed: ' + res.getResponseCode() + ' ' + res.getContentText().slice(0, 200));
  } else {
    log_('syncStatuses', Object.keys(rows).length + ' rows synced');
  }
}


/* ------------------------------------------------------------------ review page (v0.7)
 * Deploy once: Deploy > New deployment > Web app, Execute as Me, Who has access: Only myself. Bookmark the URL.
 * Shows every DRAFT post as its real slides with card, caption and credit. Approve and Kill write to the sheet.
 * Deployment never needs redoing: the Loader's doGet stub always runs the current publisher from GitHub.
 */
function doGet(e) {
  const show = (e && e.parameter && e.parameter.show) || 'DRAFT';
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  const values = sheet.getDataRange().getValues();
  const col = indexHeaders_(values[0]);
  const posts = [];
  for (let r = 1; r < values.length; r++) {
    const row = values[r];
    const status = String(row[col['Status']] || '').trim();
    if (show !== 'ALL' && status !== show) continue;
    posts.push({
      row: r + 1, index: String(row[col['Index No.']] || ''), status,
      place: cell_(row, col, 'Place'), neighborhood: cell_(row, col, 'Neighborhood'), city: cell_(row, col, 'City'),
      designer: cell_(row, col, 'Designer'), photographer: cell_(row, col, 'Photographer'),
      card: cell_(row, col, 'Card text'), caption: buildCaption_(row, col), notes: cell_(row, col, 'Notes'),
      slides: lines_(row[col['Slide links']]),
    });
  }
  const t = HtmlService.createTemplate(REVIEW_HTML);
  t.posts = posts; t.show = show; t.counts = statusCounts_(values, col);
  t.base = ScriptApp.getService().getUrl();   // absolute links: the page runs inside a Google frame
  return t.evaluate().setTitle('Bureau of Leisure: review').addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function statusCounts_(values, col) {
  const c = {};
  for (let r = 1; r < values.length; r++) { const s = String(values[r][col['Status']] || '').trim() || 'BLANK'; c[s] = (c[s] || 0) + 1; }
  return c;
}

/** Called from the review page. Sets Status on one row. Returns the new counts. */
function setStatus(rowNumber, status, index, note) {
  if (['APPROVED', 'KILLED', 'DRAFT', 'EDITS'].indexOf(status) < 0) throw new Error('Bad status');
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  const values = sheet.getDataRange().getValues();
  const col = indexHeaders_(values[0]);
  const row = values[rowNumber - 1];
  const idx = String(row[col['Index No.']] || '');
  if (idx !== String(index)) throw new Error('Row moved; reload the page');
  if (status === 'EDITS') {
    if (!note || !String(note).trim()) throw new Error('Say what to change');
    const stamp = Utilities.formatDate(new Date(), 'America/New_York', "yyyy-MM-dd HH:mm");
    const existing = cell_(row, col, 'Notes');
    sheet.getRange(rowNumber, col['Notes'] + 1).setValue(('EDITS ' + stamp + ': ' + String(note).trim() + (existing ? '\n' + existing : '')).slice(0, 5000));
  }
  sheet.getRange(rowNumber, col['Status'] + 1).setValue(status);
  log_('review', idx + ' -> ' + status + (note ? ' (' + String(note).slice(0, 80) + ')' : ''));
  return statusCounts_(sheet.getDataRange().getValues(), col);
}

const REVIEW_HTML = `<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&display=swap" rel="stylesheet">
<style>
 body{margin:0;background:#F1E9DC;color:#2A2420;font-family:"Courier Prime",Courier,monospace}
 header{position:sticky;top:0;background:#F1E9DC;border-bottom:2px solid #2A2420;padding:14px 20px;display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;z-index:2}
 header h1{font-size:16px;letter-spacing:.12em;margin:0;text-transform:uppercase}
 header a{color:#2A2420;font-size:13px;text-transform:uppercase;letter-spacing:.08em}
 header .counts{font-size:13px;margin-left:auto}
 .post{border-bottom:1px solid #2A2420;padding:22px 20px;display:grid;grid-template-columns:1fr 360px;gap:22px}
 .post.done{opacity:.35}
 .strip{display:flex;gap:10px;overflow-x:auto;padding-bottom:8px}
 .strip img{height:440px;flex:none;border:1px solid #2A2420;background:#fff}
 .meta h2{margin:0 0 4px;font-size:18px;text-transform:uppercase;letter-spacing:.06em}
 .meta .sub{font-size:13px;margin-bottom:12px}
 .meta pre{white-space:pre-wrap;font:13px/1.55 "Courier Prime",monospace;background:#fff8;border:1px solid #2A2420;padding:12px;margin:0 0 12px;max-height:260px;overflow:auto}
 .meta .notes{font-size:12px;color:#6E2A22;white-space:pre-wrap;margin-bottom:12px}
 .btns{display:flex;gap:10px}
 button{font:bold 14px "Courier Prime",monospace;letter-spacing:.1em;text-transform:uppercase;padding:12px 16px;border:2px solid #2A2420;background:#F1E9DC;color:#2A2420;cursor:pointer}
 button.approve:hover{background:#2A2420;color:#F1E9DC} button.edits:hover{background:#2A2420;color:#F1E9DC} button.kill{border-color:#6E2A22;color:#6E2A22} button.kill:hover{background:#6E2A22;color:#F1E9DC}
 button:disabled{opacity:.4;cursor:default}
 .empty{padding:60px 20px;font-size:15px}
 @media (max-width:900px){.post{grid-template-columns:1fr}.strip img{height:360px}}
</style></head><body>
<header><h1>Bureau of Leisure: review</h1>
 <? ['DRAFT','EDITS','APPROVED','KILLED','POSTED','ALL'].forEach(function(k){ ?><a href="<?= base ?>?show=<?= k ?>" target="_top" style="<?= k===show ? 'font-weight:bold;text-decoration:none' : '' ?>"><?= k.charAt(0)+k.slice(1).toLowerCase() ?></a><? }); ?>
 <span class="counts" id="counts"><?!= Object.keys(counts).map(k => k + ' ' + counts[k]).join(' · ') ?></span></header>
<? if (!posts.length) { ?><div class="empty">Nothing with status <?= show ?>.</div><? } ?>
<? posts.forEach(function(p){ ?>
<section class="post" id="p<?= p.row ?>">
 <div class="strip"><? p.slides.forEach(function(u){ ?><img loading="lazy" src="<?= u ?>"><? }); ?></div>
 <div class="meta">
  <h2>No. <?= p.index ?> · <?= p.place ?></h2>
  <div class="sub"><?= [p.neighborhood, p.city].filter(Boolean).join(', ') ?><? if (p.designer) { ?> · Design: <?= p.designer ?><? } ?><? if (p.photographer) { ?> · Photo: <?= p.photographer ?><? } ?> · <span id="s<?= p.row ?>"><?= p.status ?></span></div>
  <pre><?= p.caption ?></pre>
  <? if (p.notes) { ?><div class="notes"><?= p.notes ?></div><? } ?>
  <div class="btns">
   <button class="approve" onclick="go(<?= p.row ?>,'APPROVED','<?= p.index ?>',this)">Approve</button>
   <button class="edits" onclick="edits(<?= p.row ?>,'<?= p.index ?>')">Edits</button>
   <button class="kill" onclick="go(<?= p.row ?>,'KILLED','<?= p.index ?>',this)">Kill</button>
   <? if (p.status !== 'DRAFT') { ?><button onclick="go(<?= p.row ?>,'DRAFT','<?= p.index ?>',this)">Back to draft</button><? } ?>
  </div>
 </div>
</section>
<? }); ?>
<script>
function edits(row, index){
  const note = prompt('What should change? Plain words. e.g. "slide 2 is crooked", "drop the bed photo", "cover should be the bar shot", "word should be CLUBBY".');
  if (note && note.trim()) go(row, 'EDITS', index, null, note.trim());
}
function go(row, status, index, btn, note){
  const sec = document.getElementById('p'+row); sec.querySelectorAll('button').forEach(b => b.disabled = true);
  google.script.run.withSuccessHandler(function(counts){
    document.getElementById('s'+row).textContent = status; sec.classList.add('done');
    document.getElementById('counts').textContent = Object.keys(counts).map(k => k+' '+counts[k]).join(' · ');
  }).withFailureHandler(function(e){ alert(e.message); sec.querySelectorAll('button').forEach(b => b.disabled = false); })
  .setStatus(row, status, index, note || '');
}
</script></body></html>`;

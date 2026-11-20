// --- local-llm-host: a conversation with a local model (local_llm_host/app.py) ---
// The conversations are the server's, so they are the same on the phone and the PC.
'use strict';

const $ = id => document.getElementById(id);
const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = text => String(text).replace(/[&<>"']/g, c => ESCAPES[c]);
const TRASH_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>';
const CHAT_THOUGHTS = {
  loading: ['Loading the model…', 'Waking it up…'],
  writing: ['Thinking…', 'Finding the words…', 'Writing…'],
};
// Send: an arrow to the right; while a reply comes, a square to stop it.
const CHAT_SEND_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
const CHAT_STOP_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/></svg>';
// A cold start, about: the bar fills towards it while the model loads, then runs full while it writes.
const CHAT_LOAD_SECONDS = 20;
let chatReply = null, chatThinkingTimer = null, chatProgressTimer = null;
// The models in the models folder, and the one that replies (its id), chosen from the line under the conversation.
let chatModels = [], chatModelId = null;
// The message last tapped, for its menu: {index (none while it is still being written), role, text}.
let chatMenuMessage = null;
// How many messages the chat had as last drawn: the server checks it, so an edit or delete can't hit the wrong one.
let chatMessageCount = 0;
// Editing one of your messages: {index}, sending replaces it and the replies after it.
let chatEdit = null;
// An error said in the head stays until the next thing is sent.
let chatNoteError = false;

// A button that needs a second tap (deleting, clearing): the first arms it for a few seconds.
function secondTap(button) {
  if (button.classList.contains('armed')) {
    clearTimeout(button._armTimer);
    button.classList.remove('armed');
    return true;
  }
  button.classList.add('armed');
  button._armTimer = setTimeout(() => button.classList.remove('armed'), 3000);
  return false;
}

// --- Markdown, as the model writes it, drawn safely: everything is escaped first, and only the marks below become
// HTML. Links go only to http(s) and mailto. ---
function chatInline(text) {
  const held = [];
  const hold = html => `\u0000${held.push(html) - 1}\u0000`;
  const emphasis = part => part
    .replace(/\*\*(?=\S)([^*]*?\S)\*\*/g, '<strong>$1</strong>')
    .replace(/__(?=\S)([^_]*?\S)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\w)/g, '$1<em>$2</em>')
    .replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?!\w)/g, '$1<em>$2</em>')
    .replace(/~~(?=\S)([^~]*?\S)~~/g, '<del>$1</del>');
  const link = (url, label) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  let html = text.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code>${code}</code>`));
  html = html.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, url) =>
    /^(https?:|mailto:)/i.test(url) ? hold(link(url, emphasis(label))) : match);
  // A bare address: up to a space or an (escaped) quote or bracket, and the stop or comma after it left outside.
  html = html.replace(/(^|\s)(https?:\/\/(?:(?!&quot;|&#39;|&gt;|&lt;)[^\s])+)/g, (_, before, found) => {
    const [, url, after] = found.match(/^(.*?)((?:[.,;:!?)\]]|&amp;)*)$/);
    return before + hold(link(url, url)) + after;
  });
  return emphasis(html).replace(/\u0000(\d+)\u0000/g, (_, index) => held[Number(index)]);
}
const CHAT_FENCE = /^\s*(`{3,}|~{3,})/;
const CHAT_HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const CHAT_RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const CHAT_QUOTE = /^\s*&gt;/;
const CHAT_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const CHAT_TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
function chatCells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => chatInline(cell.trim()));
}
function chatList(items) {
  const base = items[0].indent, tag = items[0].ordered ? 'ol' : 'ul';
  let html = '', i = 0;
  while (i < items.length) {
    const item = items[i++], nested = [];
    while (i < items.length && items[i].indent > base) nested.push(items[i++]);
    html += `<li>${chatInline(item.text)}${nested.length ? chatList(nested) : ''}</li>`;
  }
  return `<${tag}>${html}</${tag}>`;
}
function chatBlocks(lines) {
  const out = [];
  let i = 0;
  const startsBlock = line => CHAT_FENCE.test(line) || CHAT_HEADING.test(line) || CHAT_RULE.test(line) || CHAT_QUOTE.test(line) || CHAT_ITEM.test(line);
  while (i < lines.length) {
    const line = lines[i];
    let match;
    if (!line.trim()) { i++; continue; }
    if ((match = line.match(CHAT_FENCE))) {
      // Still being written, a block has no end yet: the rest is its code until it comes.
      const fence = match[1], code = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) code.push(lines[i++]);
      i++;
      out.push(`<pre><code>${code.join('\n')}</code></pre>`);
    } else if ((match = line.match(CHAT_HEADING))) {
      out.push(`<h${match[1].length}>${chatInline(match[2])}</h${match[1].length}>`);
      i++;
    } else if (CHAT_RULE.test(line)) {
      out.push('<hr>');
      i++;
    } else if (CHAT_QUOTE.test(line)) {
      const quote = [];
      while (i < lines.length && CHAT_QUOTE.test(lines[i])) quote.push(lines[i++].replace(/^\s*&gt;\s?/, ''));
      out.push(`<blockquote>${chatBlocks(quote)}</blockquote>`);
    } else if (line.includes('|') && i + 1 < lines.length && CHAT_TABLE_RULE.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const head = chatCells(line), rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(chatCells(lines[i++]));
      out.push(`<div class="chat-table"><table><thead><tr>${head.map(cell => `<th>${cell}</th>`).join('')}</tr></thead>`
        + `<tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    } else if (CHAT_ITEM.test(line)) {
      const items = [];
      while (i < lines.length) {
        const item = lines[i].match(CHAT_ITEM);
        const indent = item ? item[1].replace(/\t/g, '    ').length : 0, ordered = item ? /\d/.test(item[2]) : false;
        // Bullets then numbers (or the other way) at the top: a new list.
        if (item && items.length && indent <= items[0].indent && ordered !== items[0].ordered) break;
        if (item) {
          items.push({ indent, ordered, text: item[3] });
          i++;
        } else if (lines[i].trim() && /^\s/.test(lines[i]) && items.length) {
          items[items.length - 1].text += ' ' + lines[i++].trim();  // An item's line carried on
        } else if (!lines[i].trim() && i + 1 < lines.length && CHAT_ITEM.test(lines[i + 1])) {
          i++;  // A blank line between items: still the same list
        } else break;
      }
      out.push(chatList(items));
    } else {
      const paragraph = [];
      while (i < lines.length && lines[i].trim() && (!paragraph.length || !startsBlock(lines[i]))) paragraph.push(lines[i++]);
      out.push(`<p>${paragraph.map(chatInline).join('<br>')}</p>`);
    }
  }
  return out.join('');
}
function renderMarkdown(text) {
  return chatBlocks(esc(String(text).replace(/\u0000/g, '')).replace(/\r\n?/g, '\n').split('\n'));
}

// --- The conversation ---
// An image of a message: kept on the server (its name), or one just sent, still on the page (a data: URL).
function chatImageSrc(image) {
  return /^data:image\//.test(image) ? image : `/api/chat/image/${encodeURIComponent(image)}`;
}
function chatBubble(message) {
  const ended = { stopped: 'Stopped', cut_off: 'Cut off: it ran out of room for the reply', error: 'Ended by an error' }[message.ended];
  const mine = message.role === 'user';
  const images = (message.images || []).map(image => `<img src="${esc(chatImageSrc(image))}" alt="" loading="lazy">`).join('');
  return `<div class="chat-msg ${mine ? 'user' : 'assistant'}">${images ? `<div class="chat-imgs">${images}</div>` : ''}`
    + `${mine ? esc(message.content) : renderMarkdown(message.content)}`
    + `${ended ? `<span class="chat-ended">${ended}</span>` : ''}</div>`;
}
function chatAtBottom() {
  const log = $('chatLog');
  return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
}
function chatScrollDown() { $('chatLog').scrollTop = $('chatLog').scrollHeight; }
function renderChat(messages) {
  const log = $('chatLog');
  log.innerHTML = messages.map(chatBubble).join('');
  [...log.children].forEach((bubble, index) => { bubble._raw = messages[index].content; bubble._index = index; });
  chatMessageCount = messages.length;
  chatScrollDown();
  syncChatFade();
}
// What scrolls past the top or the bottom fades out there, only where there is more to scroll to.
function syncChatFade() {
  const log = $('chatLog');
  log.classList.toggle('scroll-above', log.scrollTop > 1);
  log.classList.toggle('scroll-below', log.scrollHeight - log.clientHeight - log.scrollTop > 1);
}
$('chatLog').addEventListener('scroll', syncChatFade, { passive: true });
if (window.ResizeObserver) new ResizeObserver(syncChatFade).observe($('chatLog'));

// The saved chats (newest first), and how the open one spends the model's context, as the server last said.
let chatSessions = [], chatContextState = null, chatSessionId = null;
function applyChatState(data) {
  chatSessions = data.sessions || [];
  chatSessionId = data.id || null;
  chatModels = data.models || [];
  const has = id => chatModels.some(model => model.id === id);
  if (!has(chatModelId)) chatModelId = has(data.model) ? data.model : (chatModels[0] || {}).id || null;
  chatModelLabel();
  if (!chatReply) renderChat(data.messages || []);
  renderChatContext(data.context || null);
  // Open, it follows: one fewer after a delete, and still just above its button.
  if (!$('chatSessionsMenu').hidden) { renderChatSessions(); placeMenu($('chatSessionsMenu'), $('chatSessions')); }
}
async function api(url, options) {
  const response = await fetch(url, options);
  if (response.status === 401) { location.href = '/login'; throw new Error('Sign in again.'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || 'Could not reach local-llm-host.');
  return data;
}
async function chatRequest(url, options) {
  const data = await api(url, options);
  applyChatState(data);
  return data;
}
function loadChat() { return chatRequest('/api/chat'); }

// --- How much of the model's context the chat takes: "~5.1k / 16.4k" at the top; tapped, its parts ---
function chatTokens(count) {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(Math.round(count));
}
function renderChatContext(state) {
  chatContextState = state;
  const button = $('chatContext');
  if (!state) { button.textContent = ''; return; }
  const used = state.instruction + state.conversation;
  button.textContent = `${state.measured ? '' : '~'}${chatTokens(used)} / ${chatTokens(state.window)}`;
  // Little left before older messages start being left out.
  button.classList.toggle('tight', state.free < state.window * 0.1 || state.left_out > 0);
  if (!$('chatContextInfo').hidden) renderChatContextInfo();
}
function renderChatContextInfo() {
  const state = chatContextState, info = $('chatContextInfo');
  if (!state) { info.innerHTML = ''; return; }
  const about = state.measured ? '' : '~';
  const share = count => `${Math.max(0, count / state.window * 100).toFixed(2)}%`;
  info.innerHTML = `<span class="chat-context-bar" aria-hidden="true">`
    + `<i style="width:${share(state.instruction)};background:var(--muted)"></i>`
    + `<i style="width:${share(state.conversation)};background:var(--accent)"></i>`
    + `<i style="width:${share(state.reply)};background:var(--warn);opacity:.6"></i></span>`
    + [['Model\'s context', state.window.toLocaleString()],
       ['Instruction', `~${state.instruction.toLocaleString()}`],
       ['Conversation', `${about}${state.conversation.toLocaleString()}`],
       ['Kept for the reply', state.reply.toLocaleString()],
       ['Free', `${about}${state.free.toLocaleString()}`]].map(([name, value]) => `<span>${name}</span><span>${value}</span>`).join('')
    + `<span class="chat-context-note">${state.measured ? 'Counted by the model after its last reply.' : 'Estimated: the model counts it exactly with its next reply.'}`
    + `${state.left_out ? ` ${state.left_out} older message${state.left_out === 1 ? ' is' : 's are'} left out: the model no longer reads ${state.left_out === 1 ? 'it' : 'them'}.` : ''}</span>`;
}
$('chatContext').addEventListener('click', e => {
  e.stopPropagation();
  const open = $('chatContextInfo').hidden;
  closeMenus();
  if (!open || !chatContextState) return;
  renderChatContextInfo();
  placeMenu($('chatContextInfo'), $('chatContext'), true);
  $('chatContext').setAttribute('aria-expanded', 'true');
});

// --- Menus: each spans the screen, just above (or below) the button it came from ---
const MENUS = ['chatModelMenu', 'chatMsgMenu', 'chatSessionsMenu', 'chatContextInfo', 'appMenu'];
function placeMenu(menu, from, below = false) {
  menu.hidden = false;
  const box = from.getBoundingClientRect(), height = menu.offsetHeight;
  menu.style.top = `${below ? box.bottom + 6 : Math.max(8, box.top - height - 6)}px`;
}
function closeMenus() {
  for (const id of MENUS) $(id).hidden = true;
  for (const id of ['chatModel', 'chatSessions', 'chatContext', 'appMenuButton']) $(id).setAttribute('aria-expanded', 'false');
}
document.addEventListener('click', e => {
  if (!(e.target.closest && MENUS.some(id => e.target.closest(`#${id}`)))) closeMenus();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && MENUS.some(id => !$(id).hidden)) closeMenus();
});

// --- The saved chats: open one to carry on, or delete it ---
function renderChatSessions() {
  const when = time => {
    const date = new Date(time * 1000), today = new Date();
    return date.toDateString() === today.toDateString()
      ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : date.toLocaleDateString([], { day: 'numeric', month: 'short' });
  };
  $('chatSessionsMenu').innerHTML = chatSessions.map(session => `
    <div class="chat-session${session.id === chatSessionId ? ' current' : ''}">
      <button type="button" class="chat-session-open" role="menuitem" data-session="${esc(session.id)}" title="${esc(session.title)}">
        <span class="chat-session-title">${esc(session.title)}</span>
        <span class="chat-session-meta">${session.updated ? `${when(session.updated)} · ` : ''}${session.messages} message${session.messages === 1 ? '' : 's'}</span>
      </button>
      <button type="button" class="chat-session-delete" data-delete-session="${esc(session.id)}" aria-label="Delete this chat" title="Delete this chat (tap twice)">${TRASH_ICON}</button>
    </div>`).join('');
}
$('chatSessions').addEventListener('click', e => {
  e.stopPropagation();
  const open = $('chatSessionsMenu').hidden;
  closeMenus();
  if (!open) return;
  renderChatSessions();
  placeMenu($('chatSessionsMenu'), $('chatSessions'));
  $('chatSessions').setAttribute('aria-expanded', 'true');
});
$('chatSessionsMenu').addEventListener('click', async e => {
  const target = e.target.closest && e.target.closest('[data-session], [data-delete-session]');
  if (!target || chatReply) return;
  e.stopPropagation();
  chatNoteError = false;
  try {
    if (target.dataset.deleteSession) {
      if (!secondTap(target)) return;
      await chatRequest(`/api/chat/delete/${encodeURIComponent(target.dataset.deleteSession)}`, { method: 'POST' });
    } else {
      const data = await chatRequest(`/api/chat/open/${encodeURIComponent(target.dataset.session)}`, { method: 'POST' });
      // It carries on with the model it was having the conversation with, when that one is still there.
      if (chatModels.some(model => model.id === data.model)) { chatModelId = data.model; chatModelLabel(); }
      closeMenus();
    }
  } catch (error) { showChatError(error.message); }
  syncChat();
});

// --- Images going with the next message: + above Send, for a model that sees them ---
const CHAT_MAX_IMAGES = 4, CHAT_IMAGE_EDGE = 1536;
let chatImages = [];
function chatSees() {
  const model = chatModels.find(item => item.id === chatModelId);
  return !!(model && model.vision);
}
function renderChatAttached() {
  const box = $('chatAttached');
  box.hidden = !chatImages.length;
  box.innerHTML = chatImages.map((image, index) => `<span class="chat-thumb"><img src="${esc(image)}" alt="">`
    + `<button type="button" data-unattach="${index}" aria-label="Take this image off" title="Take it off">✕</button></span>`).join('');
  syncChat();
}
// Made smaller here (at most CHAT_IMAGE_EDGE along its longer side), so a phone's photo isn't sent at full size.
function chatImageData(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file), image = new Image();
    image.onload = () => {
      const scale = Math.min(1, CHAT_IMAGE_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL('image/jpeg', 0.9));
    };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`${file.name} isn't an image this can read.`)); };
    image.src = url;
  });
}
$('chatAttach').addEventListener('click', () => {
  if (chatImages.length >= CHAT_MAX_IMAGES) { showChatError(`Up to ${CHAT_MAX_IMAGES} images a message.`); return; }
  $('chatFile').click();
});
$('chatFile').addEventListener('change', async () => {
  const files = [...$('chatFile').files].slice(0, CHAT_MAX_IMAGES - chatImages.length);
  $('chatFile').value = '';
  chatNoteError = false;
  for (const file of files) {
    try { chatImages.push(await chatImageData(file)); } catch (e) { showChatError(e.message); }
  }
  renderChatAttached();
});
$('chatAttached').addEventListener('click', e => {
  const button = e.target.closest && e.target.closest('[data-unattach]');
  if (!button) return;
  e.stopPropagation();
  chatImages.splice(Number(button.dataset.unattach), 1);
  renderChatAttached();
});

// --- The model, chosen from the line under the conversation ---
function chatModelLabel() {
  const model = chatModels.find(item => item.id === chatModelId);
  $('chatModel').textContent = model ? model.label : 'No models';
  $('chatModel').title = model ? `${model.label}${model.vision ? ' (sees images)' : ''}: tap to change` : 'Put GGUF files in the models folder';
}
$('chatModel').addEventListener('click', e => {
  e.stopPropagation();
  const open = $('chatModelMenu').hidden;
  closeMenus();
  if (!open || !chatModels.length) return;
  $('chatModelMenu').innerHTML = chatModels.map(model => {
    const active = model.id === chatModelId;
    return `<button type="button" role="menuitemradio" aria-checked="${active}" class="${active ? 'selected' : ''}" data-model="${esc(model.id)}">`
      + `${esc(model.label)}${model.vision ? '<span class="chat-model-tag">vision</span>' : ''}</button>`;
  }).join('');
  placeMenu($('chatModelMenu'), $('chatModel'));
  $('chatModel').setAttribute('aria-expanded', 'true');
});
$('chatModelMenu').addEventListener('click', e => {
  const choice = e.target.closest && e.target.closest('[data-model]');
  if (!choice) return;
  chatModelId = choice.dataset.model;
  chatModelLabel();
  closeMenus();
  syncChat();
});

// --- A message tapped: a menu where it was tapped, of what can be done with it ---
// The text without its Markdown marks: what another app should get.
function chatPlainText(text) {
  return String(text)
    .replace(/^[ \t]*(`{3,}|~{3,}).*(\n|$)/gm, '')
    .replace(/^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*(\n|$)/gm, '')
    .replace(/^[ \t]*\|(.*)\|[ \t]*$/gm, (_, row) => row.split('|').map(cell => cell.trim()).join('\t'))
    .replace(/^#{1,6}[ \t]+/gm, '')
    .replace(/^[ \t]*>[ \t]?/gm, '')
    .replace(/^([ \t]*)[-*+][ \t]+/gm, '$1• ')
    .replace(/!?\[([^\]\n]+)\]\(([^)\s]+)\)/g, '$1 ($2)')
    .replace(/(\*\*|__|~~)(?=\S)([^\n]*?\S)\1/g, '$2')
    .replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\w)/g, '$1$2')
    .replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?!\w)/g, '$1$2')
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
function chatMenuItems(message) {
  const items = [['copy', 'Copy all text'], ['plain', 'Copy as plain text']];
  // A message still being written, or while a reply comes, is only read: nothing in the chat is changed meanwhile.
  if (message.index == null || chatReply) return items;
  items.push(message.role === 'assistant' ? ['redo', 'Regenerate'] : ['edit', 'Edit and resend'], ['delete', 'Delete']);
  return items;
}
$('chatLog').addEventListener('click', e => {
  const bubble = e.target.closest && e.target.closest('.chat-msg');
  // A link opens; text being selected stays selected, to copy just that.
  if (!bubble || e.target.closest('a') || String(window.getSelection ? window.getSelection() : '').trim()) return;
  e.stopPropagation();
  closeMenus();
  chatMenuMessage = { index: bubble._index, role: bubble.classList.contains('user') ? 'user' : 'assistant',
                      text: bubble._raw != null ? bubble._raw : bubble.textContent };
  const menu = $('chatMsgMenu');
  menu.innerHTML = chatMenuItems(chatMenuMessage).map(([action, label]) =>
    `<button type="button" role="menuitem" data-chat-action="${action}"${action === 'delete' ? ' class="danger-item"' : ''}>${label}</button>`).join('');
  menu.hidden = false;
  const width = menu.offsetWidth, height = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(e.clientX, window.innerWidth - width - 8))}px`;
  menu.style.top = `${e.clientY + 8 + height > window.innerHeight - 8 ? Math.max(8, e.clientY - height - 8) : e.clientY + 8}px`;
});
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch (e) { /* Not over HTTPS (from a phone on the home network): copied the old way below */ }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
  document.body.appendChild(area);
  area.select();
  try { document.execCommand('copy'); } finally { area.remove(); }
}
function setChatEdit(edit) {
  chatEdit = edit;
  $('chatEditing').hidden = !edit;
}
$('chatEditCancel').addEventListener('click', () => {
  setChatEdit(null);
  $('chatInput').value = '';
  syncChat();
});
$('chatMsgMenu').addEventListener('click', async e => {
  const button = e.target.closest && e.target.closest('[data-chat-action]');
  const message = chatMenuMessage;
  if (!button || !message) return;
  e.stopPropagation();
  const action = button.dataset.chatAction;
  // Dropping replies after it, or the message itself: a second tap says so.
  const after = message.index != null ? chatMessageCount - message.index - 1 : 0;
  if ((action === 'delete' || (action === 'redo' && after > 0)) && !secondTap(button)) return;
  chatNoteError = false;
  try {
    if (action === 'copy' || action === 'plain') {
      await copyText(action === 'plain' ? chatPlainText(message.text) : message.text);
      button.textContent = 'Copied';
      setTimeout(closeMenus, 600);
      return;
    }
    closeMenus();
    if (action === 'edit') {
      setChatEdit({ index: message.index });
      $('chatInput').value = message.text;
    } else if (action === 'redo') await chatReplyTo(null, message.index);
    else if (action === 'delete') {
      await chatRequest('/api/chat/message/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: message.index, count: chatMessageCount }) });
      if (chatEdit && chatEdit.index >= message.index) setChatEdit(null);
    }
  } catch (error) { showChatError(error.message); }
  syncChat();
});

// --- Sending, and the reply as it is written ---
function syncChat() {
  const send = $('chatSend'), action = chatReply ? 'Stop' : 'Send';
  if (send.getAttribute('aria-label') !== action || !send.innerHTML) {
    send.innerHTML = chatReply ? CHAT_STOP_ICON : CHAT_SEND_ICON;
    send.setAttribute('aria-label', action);
    send.title = action;
  }
  // Images for a model that can't see them: not sent, said why.
  const blind = chatImages.length > 0 && !chatSees();
  send.disabled = !chatReply && (blind || !($('chatInput').value.trim() || chatImages.length) || !chatModelId);
  send.classList.toggle('ready', !chatReply && !send.disabled);
  $('chatAttach').disabled = !!chatReply;
  $('chatModel').disabled = $('chatClear').disabled = $('chatSessions').disabled = $('chatNew').disabled = !!chatReply;
  if (chatReply) {
    $('chatNote').textContent = chatReply.status === 'loading'
      ? `Loading · ${Math.ceil(Math.max(0, CHAT_LOAD_SECONDS - (performance.now() - chatReply.started) / 1000))} s left`
      : chatReply.speed ? `${chatReply.speed.toFixed(1)} tok/s` : '';
  } else if (!chatNoteError) {
    $('chatNote').textContent = !chatModels.length ? 'No models: put GGUF files in the models folder.'
      : blind ? "This model can't see images: choose one marked vision." : '';
  }
}
function showChatError(message) {
  chatNoteError = true;
  $('chatNote').textContent = message;
}
function setChatThinking(status) {
  clearInterval(chatThinkingTimer);
  chatThinkingTimer = null;
  const box = $('chatThinking');
  $('chatTitle').hidden = !!status;
  box.hidden = !status;
  if (!status) { box.textContent = ''; return; }
  let index = -1;
  const advance = () => {
    index = (index + 1) % CHAT_THOUGHTS[status].length;
    box.textContent = CHAT_THOUGHTS[status][index];
  };
  advance();
  chatThinkingTimer = setInterval(advance, 2200);
}
function setChatProgress(status) {
  clearInterval(chatProgressTimer);
  chatProgressTimer = null;
  const bar = $('chatProgress');
  bar.hidden = !status;
  if (!status) { bar.style.width = '0%'; return; }
  if (status === 'writing') { bar.style.width = '100%'; return; }
  const start = performance.now();
  const update = () => { bar.style.width = `${Math.min(95, (performance.now() - start) / 1000 / CHAT_LOAD_SECONDS * 100)}%`; };
  update();
  chatProgressTimer = setInterval(update, 1000);
}
function setChatStatus(status) {
  if (!chatReply || chatReply.status === status) return;
  chatReply.status = status;
  if (status === 'loading') chatReply.started = performance.now();
  setChatThinking(status);
  setChatProgress(status);
  syncChat();
}
function sendChat() {
  const text = $('chatInput').value.trim();
  if (!text && !chatImages.length) return;
  if (chatImages.length && !chatSees()) return;
  chatReplyTo(text, chatEdit ? chatEdit.index : null, chatImages);
}
// A reply to text, sent as your next message; from: the conversation goes on from that message instead, what came
// from it on dropped (your message there changed, or with no text, its reply written again).
async function chatReplyTo(text, from = null, images = []) {
  const model = chatModelId;
  if ((!text && !images.length && from == null) || !model || chatReply) return;
  chatNoteError = false;
  closeMenus();
  const count = chatMessageCount;
  const reply = chatReply = { controller: new AbortController(), started: performance.now(), status: null };
  if (text || images.length) {
    $('chatInput').value = '';
    chatImages = [];
    renderChatAttached();
  }
  setChatEdit(null);
  // What is dropped goes from the screen at once; the server's copy is drawn again once the reply is in.
  if (from != null) [...$('chatLog').children].forEach(bubble => { if (bubble._index >= from) bubble.remove(); });
  if (text || images.length) {
    $('chatLog').insertAdjacentHTML('beforeend', chatBubble({ role: 'user', content: text, images }));
    $('chatLog').lastElementChild._raw = text;
  }
  $('chatLog').insertAdjacentHTML('beforeend', '<div class="chat-msg assistant writing"></div>');
  const bubble = $('chatLog').lastElementChild;
  bubble._raw = '';
  chatScrollDown();
  setChatStatus('writing');
  // Drawn once a frame, however many pieces came in it: the Markdown is drawn again from the whole reply each time.
  let drawing = false;
  const draw = () => {
    if (drawing) return;
    drawing = true;
    const follow = chatAtBottom();
    requestAnimationFrame(() => {
      drawing = false;
      bubble.innerHTML = renderMarkdown(bubble._raw);
      if (follow) chatScrollDown();
      syncChatFade();
    });
  };
  let error = null;
  try {
    const response = await fetch('/api/chat/send', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text || '', model, images, ...(from != null ? { from, count } : {}) }), signal: reply.controller.signal,
    });
    if (response.status === 401) { location.href = '/login'; return; }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'Could not send the message.');
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).trim();
        buffer = buffer.slice(end + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.status) setChatStatus(event.status);
        if (event.speed) { reply.speed = event.speed; syncChat(); }
        if (event.text) { bubble._raw += event.text; draw(); }
        if (event.error) error = event.error;
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') error = e.message || 'Could not reach local-llm-host.';
  } finally {
    chatReply = null;
    setChatThinking(null);
    setChatProgress(null);
    if (error) showChatError(error);
    // The server's copy is the conversation: what was kept of the reply, marked if it was stopped or cut off.
    try { await loadChat(); } catch (e) { if (!error) showChatError(e.message); }
    syncChat();
  }
}
$('chatSend').addEventListener('click', () => {
  if (chatReply) chatReply.controller.abort(); else sendChat();
});
$('chatInput').addEventListener('input', syncChat);
// Enter sends on a keyboard; on a phone it is a new line, and Send sends.
$('chatInput').addEventListener('keydown', e => {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing || window.matchMedia('(pointer: coarse)').matches) return;
  e.preventDefault();
  sendChat();
});
$('chatClear').addEventListener('click', async () => {
  const button = $('chatClear');
  if (chatReply || !secondTap(button)) return;
  chatNoteError = false;
  try { await chatRequest('/api/chat/clear', { method: 'POST' }); } catch (e) { showChatError(e.message); }
  syncChat();
});
$('chatNew').addEventListener('click', async () => {
  if (chatReply) return;
  chatNoteError = false;
  closeMenus();
  try { await chatRequest('/api/chat/new', { method: 'POST' }); } catch (e) { showChatError(e.message); }
  syncChat();
});

// --- The app's menu: the model loaded, the addresses for other devices, sign out and shut down ---
async function openAppMenu() {
  const menu = $('appMenu');
  let status;
  try { status = await api('/api/status'); } catch (e) { showChatError(e.message); return; }
  const address = (url, label) => `<div class="menu-line menu-address"><span>${label}<br><code>${esc(url)}</code></span>`
    + `<button type="button" class="icon-btn" data-copy="${esc(url)}" aria-label="Copy ${label} address" title="Copy">`
    + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg></button></div>';
  let html = '<div class="menu-section">Model</div>';
  if (status.replying) html += '<div class="menu-line">Writing a reply…</div>';
  else if (status.loaded) html += `<div class="menu-line">${esc(status.loaded.label)} is loaded.</div><button type="button" data-menu="unload">Unload it now</button>`;
  else html += '<div class="menu-line muted">None loaded: the next message loads one.</div>';
  if (!status.server_set) html += '<div class="menu-line muted">Set LLAMA_SERVER in .env to the llama-server program from llama.cpp.</div>';
  html += '<div class="menu-section">Other devices</div>';
  if (status.tailscale) html += address(status.tailscale, 'Anywhere (Tailscale)');
  if (status.lan) html += address(status.lan, 'Home network');
  if (!status.tailscale && !status.lan) {
    html += `<div class="menu-line muted">${status.protected ? (status.tailscale_problem || 'Only this PC: start it with --host 0.0.0.0 (the shortcut does).')
      : 'Only this PC: set APP_PASSWORD in .env to let your other devices in.'}</div>`;
  } else if (status.tailscale_problem) html += `<div class="menu-line muted">${esc(status.tailscale_problem)}</div>`;
  html += '<div class="menu-section">App</div>';
  if (status.protected) html += '<a class="menu-link" href="/logout">Sign out</a>';
  html += '<button type="button" class="danger-item" data-menu="shutdown" title="Stops the model and the app (tap twice)">Shut down</button>';
  html += `<div class="menu-version">local-llm-host ${esc(status.version)}</div>`;
  menu.innerHTML = html;
  placeMenu(menu, $('appMenuButton'), true);
  $('appMenuButton').setAttribute('aria-expanded', 'true');
}
$('appMenuButton').addEventListener('click', e => {
  e.stopPropagation();
  const open = $('appMenu').hidden;
  closeMenus();
  if (open) openAppMenu();
});
$('appMenu').addEventListener('click', async e => {
  const button = e.target.closest && e.target.closest('[data-menu], [data-copy]');
  if (!button) return;
  e.stopPropagation();
  try {
    if (button.dataset.copy) {
      await copyText(button.dataset.copy);
      button.classList.add('armed');
      setTimeout(() => button.classList.remove('armed'), 800);
    } else if (button.dataset.menu === 'unload') {
      await api('/api/unload', { method: 'POST' });
      closeMenus();
    } else if (button.dataset.menu === 'shutdown') {
      if (!secondTap(button)) return;
      await api('/api/shutdown', { method: 'POST' });
      closeMenus();
      document.body.innerHTML = '<div class="login"><h1>local-llm-host has shut down.</h1><p class="sub">Start it again from its shortcut.</p></div>';
    }
  } catch (error) { showChatError(error.message); }
});

// The page fits what the keyboard leaves of the screen.
function fitPage() {
  const view = window.visualViewport;
  if (!view) return;
  $('app').style.top = `${Math.round(view.offsetTop)}px`;
  $('app').style.height = `${Math.round(view.height)}px`;
}
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', fitPage);
  window.visualViewport.addEventListener('scroll', fitPage);
}
// Back to the tab: what another device changed meanwhile.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !chatReply) loadChat().catch(() => {});
});

fitPage();
syncChat();
loadChat().catch(e => showChatError(e.message)).finally(syncChat);

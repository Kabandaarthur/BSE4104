const form = document.querySelector('#chat-form');
const input = document.querySelector('#message-input');
const conversation = document.querySelector('#conversation');
const sendButton = document.querySelector('#send-button');
const resetButton = document.querySelector('#reset-button');
const welcomeBlock = document.querySelector('#welcome-block');
const caseValue = document.querySelector('#case-value');
const caseIndicator = document.querySelector('.case-indicator');
const serviceState = document.querySelector('.service-state');
const serviceLabel = document.querySelector('#service-label');

function addText(parent, tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  parent.append(element);
  return element;
}

function addMessage(role, text, response = null) {
  welcomeBlock.hidden = true;
  const message = document.createElement('article');
  message.className = `message ${role}`;
  const avatar = addText(message, 'span', 'message-avatar', role === 'assistant' ? 'M' : 'Y');
  avatar.setAttribute('aria-hidden', 'true');
  const body = document.createElement('div');
  body.className = 'message-body';
  addText(body, 'p', 'message-label', role === 'assistant' ? 'Student support' : 'You');
  addText(body, 'p', 'message-text', text);

  if (response) {
    const entries = [];
    for (const source of response.sources || []) entries.push({ label: `Source: ${source}`, kind: 'source' });
    for (const tool of response.tools_used || []) {
      const status = tool.status === 'success' ? 'completed' : 'unavailable';
      entries.push({ label: `${tool.name.replaceAll('_', ' ')} · ${status}`, kind: tool.status === 'success' ? '' : 'pending' });
    }
    if (response.active_case_id) {
      entries.push({ label: `Active case: ${response.active_case_id}`, kind: 'pending' });
      updateCase(response.active_case_id);
    }
    if (entries.length) {
      const details = document.createElement('details');
      details.className = 'details';
      const summary = addText(details, 'summary', '', `References and actions (${entries.length})`);
      summary.setAttribute('aria-label', `Show ${entries.length} sources and tool actions`);
      const content = document.createElement('div');
      content.className = 'details-content';
      for (const entry of entries) addText(content, 'span', `detail-tag ${entry.kind}`.trim(), entry.label);
      details.append(content);
      body.append(details);
    }
    if (response.active_case_id !== undefined) updateCase(response.active_case_id);
  }

  message.append(body);
  conversation.append(message);
  conversation.scrollTop = conversation.scrollHeight;
  return message;
}

function updateCase(caseId) {
  caseValue.textContent = caseId || 'No case linked to this session';
  caseValue.classList.toggle('case-empty', !caseId);
  caseIndicator.dataset.active = String(Boolean(caseId));
}

async function checkService() {
  try {
    const response = await fetch('/health');
    if (!response.ok) throw new Error('Service unavailable');
    serviceLabel.textContent = 'Service online';
    serviceState.dataset.state = 'ok';
  } catch {
    serviceLabel.textContent = 'Service unavailable';
    serviceState.dataset.state = 'error';
  }
}

async function sendMessage(message) {
  const cleanMessage = message.trim();
  if (!cleanMessage || sendButton.disabled) return;

  addMessage('user', cleanMessage);
  input.value = '';
  input.style.height = 'auto';
  sendButton.disabled = true;
  const pending = document.createElement('article');
  pending.className = 'message assistant';
  addText(pending, 'span', 'message-avatar', 'M').setAttribute('aria-hidden', 'true');
  addText(pending, 'p', 'typing', 'Checking the available guidance...');
  conversation.append(pending);
  conversation.scrollTop = conversation.scrollHeight;

  try {
    const response = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ message: cleanMessage }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || 'The request could not be completed.');
    pending.remove();
    addMessage('assistant', payload.reply, payload);
  } catch (error) {
    pending.remove();
    addMessage('assistant', `Sorry, I could not complete that request. ${error.message}`);
  } finally {
    sendButton.disabled = false;
    input.focus();
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
});

input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll('.suggestion').forEach((button) => {
  button.addEventListener('click', () => sendMessage(button.dataset.message));
});

resetButton.addEventListener('click', async () => {
  if (sendButton.disabled) return;
  resetButton.disabled = true;
  try {
    const response = await fetch('/session/reset', { method: 'POST', credentials: 'same-origin' });
    const payload = await response.json();
    if (response.ok) {
      conversation.replaceChildren(welcomeBlock);
      welcomeBlock.hidden = false;
      updateCase(null);
      input.value = '';
    } else if (response.status === 404) {
      conversation.replaceChildren(welcomeBlock);
      welcomeBlock.hidden = false;
      updateCase(null);
      addMessage('assistant', 'This app is running without session memory, so no case context was saved. The conversation has been cleared.');
    } else {
      throw new Error(payload.detail || 'Could not reset this session.');
    }
  } catch (error) {
    addMessage('assistant', `Could not reset this session. ${error.message}`);
  } finally {
    resetButton.disabled = false;
    input.focus();
  }
});

checkService();
const box = document.getElementById('ai-runtime');

function row(label, value) {
  const p = document.createElement('p');
  const strong = document.createElement('strong');
  strong.textContent = label + ': ';
  p.append(strong, document.createTextNode(value));
  return p;
}

function note(text, kind = '') {
  const p = document.createElement('p');
  p.className = kind || 'muted';
  p.textContent = text;
  return p;
}

async function loadProvider() {
  if (!box) return;
  box.replaceChildren(
    Object.assign(document.createElement('h2'), {textContent: 'AI runtime'}),
    note('Checking the school API provider…')
  );
  try {
    const response = await fetch('/api/assessment-run', {
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error'
    });
    let data = {};
    try { data = await response.json(); } catch {}
    if (!response.ok) throw new Error(data.error || 'provider status unavailable');

    const title = Object.assign(document.createElement('h2'), {textContent: 'AI runtime'});
    const provider = data.provider === 'openai' ? 'OpenAI API' :
      data.provider === 'anthropic' ? 'Anthropic API' : (data.provider || 'Not selected');
    const configured = data.configured ? 'Ready' : 'Not configured';

    const parts = [
      title,
      row('Provider', provider),
      row('Model', data.model || '—'),
      row('Status', configured),
    ];

    if (!data.configured) {
      parts.push(note(
        data.dependency === 'runtime_key_missing'
          ? 'The API secret is not attached to the deployed Hub yet. Deterministic grading, deck checks, link audits and queue work can still run; semantic feedback waits.'
          : 'The deployed API secret is not valid for this provider. Semantic feedback waits until the secret binding is fixed.',
        'needs'
      ));
    } else {
      parts.push(note(
        'This is API billing, separate from your ChatGPT subscription. The Hub should use AI only for semantic judgment; counting, routing, deck-code checks, grade arithmetic and report assembly stay in Python.'
      ));
    }
    parts.push(note(
      'Cost rule: reuse cached drafts for unchanged work; process only changed submissions; keep long teacher prompts stable so provider prompt caching can reduce repeated input cost.'
    ));
    box.replaceChildren(...parts);
  } catch (error) {
    box.replaceChildren(
      Object.assign(document.createElement('h2'), {textContent: 'AI runtime'}),
      note('AI provider status could not be read. This does not prove the provider is down; reload after signing in to the Hub.', 'needs')
    );
  }
}

function maybeLoad() {
  if (location.hash === '#routine' || !location.hash) loadProvider();
}

window.addEventListener('hashchange', maybeLoad);
document.getElementById('reload-routine')?.addEventListener('click', loadProvider);
maybeLoad();

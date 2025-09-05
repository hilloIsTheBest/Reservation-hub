(() => {
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const homesEl = $('#homes');
  const resSel = $('#homeResourceFilter');
  const newHomeName = $('#newHomeName');
  const createHomeBtn = $('#createHome');
  const activeHomeName = $('#activeHomeName');
  const ownerPanel = $('#ownerPanel');
  const userPicker = $('#userPicker');
  const addMemberBtn = $('#addMember');
  const memberChips = $('#memberChips');
  const icsEl = $('#homeIcs');
  const themeSel = $('#themeSel');
  const syncBtn = $('#syncBtn');

  // Theme handling
  const themeKey = 'ff_theme';
  const applyTheme = (t) => {
    document.body.classList.remove('theme-dark', 'theme-hue');
    if (t === 'dark') document.body.classList.add('theme-dark');
    if (t === 'hue') document.body.classList.add('theme-hue');
    themeSel.value = t;
    localStorage.setItem(themeKey, t);
  };
  applyTheme(localStorage.getItem(themeKey) || 'light');
  themeSel.addEventListener('change', () => applyTheme(themeSel.value));

  let state = {
    homes: [],
    activeHomeId: null,
    activeHomeIsOwner: false,
    resources: [],
    users: [],
    calendar: null
  };

  async function loadHomes() {
    const r = await fetch('/api/homes');
    if (!r.ok) return;
    state.homes = await r.json();
    renderHomes();
  }

  function renderHomes() {
    homesEl.innerHTML = '';
    state.homes.forEach(h => {
      const div = document.createElement('div');
      div.className = 'home-item' + (state.activeHomeId === h.id ? ' active' : '');
      div.innerHTML = `<div>${h.name}</div><div class="chip">${h.is_owner ? 'Owner' : 'Member'}</div>`;
      div.onclick = () => setActiveHome(h.id, h.name, h.is_owner);
      homesEl.appendChild(div);
    });
  }

  createHomeBtn.onclick = async () => {
    if (!isAuthed) return alert('Please log in first.');
    const name = newHomeName.value.trim();
    if (!name) return alert('Name required');
    const r = await fetch('/api/homes', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name}) });
    if (!r.ok) {
      const j = await r.json().catch(()=>({detail:'Error'}));
      return alert('Failed: '+(j.detail||r.status));
    }
    newHomeName.value = '';
    await loadHomes();
  };

  async function setActiveHome(id, name, isOwner) {
    state.activeHomeId = id; state.activeHomeIsOwner = isOwner;
    activeHomeName.textContent = name;
    icsEl.textContent = new URL(`/ics/home/${id}.ics`, location.origin);
    await Promise.all([loadHomeResources(), loadHomeDetails()]);
    initCalendar();
    renderHomes();
  }

  async function loadHomeResources() {
    const r = await fetch(`/api/homes/${state.activeHomeId}/resources`);
    state.resources = r.ok ? await r.json() : [];
    resSel.innerHTML = `<option value="">All</option>` + state.resources.map(r=>`<option value="${r.id}">${r.name}</option>`).join('');
  }

  async function loadUsers() {
    if (state.users.length) return;
    const r = await fetch('/api/users');
    state.users = r.ok ? await r.json() : [];
  }

  async function loadHomeDetails() {
    const r = await fetch(`/api/homes/${state.activeHomeId}`);
    if (!r.ok) { ownerPanel.style.display = 'none'; return; }
    const j = await r.json();
    ownerPanel.style.display = j.is_owner ? '' : 'none';
    if (j.is_owner) {
      await loadUsers();
      userPicker.innerHTML = state.users.map(u=>`<option value="${u.id}">${u.name} — ${u.email}</option>`).join('');
      memberChips.innerHTML = j.members.map(m=>`<span class="chip" data-id="${m.id}">${m.name} <a href="#" data-act="rm">✕</a></span>`).join('');
      memberChips.querySelectorAll('a[data-act="rm"]').forEach(a => {
        a.onclick = async (ev) => {
          ev.preventDefault();
          const id = a.parentElement.getAttribute('data-id');
          if (!confirm('Remove member?')) return;
          const resp = await fetch(`/api/homes/${state.activeHomeId}/members/${id}`, { method:'DELETE' });
          if (!resp.ok) {
            const jj = await resp.json().catch(()=>({detail:'Error'}));
            return alert('Failed: '+(jj.detail||resp.status));
          }
          await loadHomeDetails();
        };
      });
    }
  }

  addMemberBtn.onclick = async () => {
    const user_id = userPicker.value;
    if (!user_id) return;
    const resp = await fetch(`/api/homes/${state.activeHomeId}/members`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({user_id}) });
    if (!resp.ok) {
      const j = await resp.json().catch(()=>({detail:'Error'}));
      return alert('Failed: '+(j.detail||resp.status));
    }
    await loadHomeDetails();
  };

  function initCalendar() {
    const el = document.getElementById('calendar');
    if (state.calendar) { state.calendar.destroy(); state.calendar = null; }
    state.calendar = new FullCalendar.Calendar(el, {
      initialView: 'timeGridWeek',
      timeZone: 'local', nowIndicator: true, selectable: true,
      headerToolbar: { left:'prev,next today', center:'title', right:'dayGridMonth,timeGridWeek,timeGridDay' },
      events: (info, success, failure) => {
        const params = new URLSearchParams({ start: info.start.toISOString(), end: info.end.toISOString() });
        if (resSel.value) params.append('resource_id', resSel.value);
        fetch(`/api/homes/${state.activeHomeId}/events?`+params).then(r=>r.json()).then(success).catch(failure);
      },
      select: async (sel) => {
        if (!isAuthed) { alert('Please log in first.'); return; }
        const title = prompt('Reservation title?'); if (!title) { state.calendar.unselect(); return; }
        let resource_id = resSel.value;
        if (!resource_id) {
          const names = state.resources.map((r,i)=>`${i+1}. ${r.name}`).join('\n');
          const idx = prompt('Pick a resource:\n'+names);
          const i = parseInt(idx)-1; if (isNaN(i) || i<0) return alert('Invalid');
          resource_id = state.resources[i].id;
        }
        let repeat = prompt('Repeat? none / daily / weekly', 'none');
        repeat = (repeat||'none').toLowerCase().trim();
        let rrule = '';
        if (repeat === 'daily') rrule = 'FREQ=DAILY';
        if (repeat === 'weekly') {
          const days = ['SU','MO','TU','WE','TH','FR','SA'];
          const byday = days[sel.start.getUTCDay()];
          rrule = `FREQ=WEEKLY;BYDAY=${byday}`;
        }
        const payload = { title, resource_id, start: sel.start.toISOString(), end: sel.end.toISOString(), rrule };
        const resp = await fetch(`/api/homes/${state.activeHomeId}/bookings`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
        if (!resp.ok) {
          const j = await resp.json().catch(()=>({detail:'Error'}));
          alert('Failed: '+(j.detail||resp.status));
        }
        state.calendar.refetchEvents();
      },
      eventClick: async (info) => {
        if (!confirm('Delete this reservation?')) return;
        const id = info.event.id; // series id for recurring
        const resp = await fetch(`/api/homes/${state.activeHomeId}/bookings/${id}`, { method:'DELETE' });
        if (!resp.ok) {
          const j = await resp.json().catch(()=>({detail:'Error'}));
          return alert('Failed: '+(j.detail||resp.status));
        }
        state.calendar.refetchEvents();
      }
    });
    state.calendar.render();
  }

  resSel.addEventListener('change', () => state.calendar && state.calendar.refetchEvents());

  // boot
  (async () => {
    await loadHomes();
    if (state.homes.length) setActiveHome(state.homes[0].id, state.homes[0].name, state.homes[0].is_owner);
  })();

  // offline sync
  if (syncBtn) {
    syncBtn.onclick = async () => {
      const url = prompt('Enter server URL to sync from (e.g., https://your-server.example.com):');
      if (!url) return;
      try {
        const r = await fetch('/offline/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_url: url }) });
        const j = await r.json().catch(()=>({ok:false, error:'Invalid response'}));
        if (!r.ok || !j.ok) {
          alert('Sync failed: ' + (j.error || r.status));
          return;
        }
        alert(`Synced resources: ${j.resources_created} added, ${j.resources_updated} updated.\nImported events: ${j.events_imported}.`);
        // Reload current home resources/events
        if (state.activeHomeId) {
          await loadHomeResources();
          if (state.calendar) state.calendar.refetchEvents();
        } else {
          await loadHomes();
        }
      } catch (e) {
        alert('Sync error: ' + e);
      }
    };
  }
})();

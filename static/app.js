(async function () {
  const resSel = document.getElementById('resourceFilter');
  const icsAllEl = document.getElementById('icsAll');
  if (icsAllEl) icsAllEl.textContent = new URL('/ics/all.ics', location.origin);

  async function loadResources() {
    const rs = await fetch('/api/resources').then(r=>r.json());
    resSel.innerHTML = `<option value="">All</option>` + rs.map(r=>`<option value="${r.id}">${r.name}</option>`).join('');
    return rs;
  }

  const resources = await loadResources();

  const calendar = new FullCalendar.Calendar(document.getElementById('calendar'), {
    initialView: 'timeGridWeek',
    timeZone: 'local',
    nowIndicator: true,
    selectable: true,
    headerToolbar: { left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek,timeGridDay' },
    events: function(info, success, failure) {
      // Send UTC to backend to avoid ambiguity
      const params = new URLSearchParams({ start: info.start.toISOString(), end: info.end.toISOString() });
      if (resSel.value) params.append('resource_id', resSel.value);
      fetch('/api/events?'+params).then(r=>r.json()).then(success).catch(failure);
    },
    select: async function(sel) {
      if (!isAuthed) { alert('Please log in first.'); return; }
      const title = prompt('Reservation title?'); if (!title) { calendar.unselect(); return; }
      let resource_id = resSel.value;
      if (!resource_id) {
        const names = (await fetch('/api/resources').then(r=>r.json())).map((r,i)=>`${i+1}. ${r.name}`).join('\n');
        const idx = prompt('Pick a resource:\n'+names);
        const i = parseInt(idx)-1; if (isNaN(i) || i<0) return alert('Invalid');
        resource_id = (await fetch('/api/resources').then(r=>r.json()))[i].id;
      }
      // Send UTC to backend to avoid ambiguity
      const payload = { title, resource_id, start: sel.start.toISOString(), end: sel.end.toISOString() };
      const resp = await fetch('/api/bookings', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      if (!resp.ok) {
        const j = await resp.json().catch(()=>({detail:'Error'}));
        alert('Failed: '+(j.detail||resp.status));
      }
      calendar.refetchEvents();
    }
  });
  calendar.render();
  resSel.addEventListener('change', ()=> calendar.refetchEvents());

  if (isAdmin) {
    document.getElementById('addResource').onclick = async () => {
      const name = document.getElementById('newResName').value.trim();
      const color = document.getElementById('newResColor').value;
      if (!name) return alert('Name required');
      const r = await fetch('/api/resources', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, color}) });
      if (!r.ok) {
        const j = await r.json().catch(()=>({detail:'Error'}));
        return alert('Failed: '+(j.detail||r.status));
      }
      await loadResources();
      alert('Resource added');
    };
  }
})();

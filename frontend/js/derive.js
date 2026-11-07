// Производные данные из транскрипта: лента событий, контекст бота, удержание.
// Работает и в живом режиме как запасной вариант, если бэкенд не прислал timeline/bot_context.

const RX = {
  escalation: /эскалац|операторға жіберілді|transferred to an agent|агент присоединился|переведен от агента/i,
  identity: /личность клиента подтвержд|жеке басы расталды|identity verified/i,
  holdStart: /находится на удержании/i,
  holdEnd: /больше не находится на удержании/i,
  clientLeft: /клиент покинул|customer left|client left/i,
  callEnded: /завершил разговор|call ended/i,
  lost: /прерван|connection to customer lost|байланыс үзілді/i,
  operatorClosed: /оператор закрыл|agent closed|оператор чатты жапты/i,
};

export function deriveTimeline(transcript, evaluation) {
  const ev = [];
  const firstOp = transcript.find((m) => m.speaker === 'operator' && !m.on_hold);
  const esc = transcript.find((m) => m.speaker === 'system' && RX.escalation.test(m.text));
  if (esc) ev.push({ type: 'escalation', index: esc.index });
  if (firstOp) ev.push({ type: 'operator_greeting', index: firstOp.index });
  const id = transcript.find((m) => m.speaker === 'system' && RX.identity.test(m.text));
  if (id) ev.push({ type: 'identity_verified', index: id.index });
  else if (evaluation) {
    const c03 = evaluation.criteria?.find((c) => c.criterion_id === 'c03');
    if (c03?.answer === 'yes' && c03.evidence?.length) ev.push({ type: 'identity_verified', index: c03.evidence.at(-1).index });
  }
  if (evaluation) {
    const g = evaluation.gross_error;
    if (g?.flag && g.evidence?.length) ev.push({ type: 'risk', index: g.evidence[0].index, kinds: g.types });
  }
  const hold = transcript.find((m) => m.speaker === 'system' && RX.holdStart.test(m.text) && !RX.holdEnd.test(m.text));
  if (hold) ev.push({ type: 'hold', index: hold.index });
  const last = [...transcript].reverse().find((m) => m.speaker === 'system' && (RX.clientLeft.test(m.text) || RX.lost.test(m.text) || RX.operatorClosed.test(m.text) || RX.callEnded.test(m.text)));
  if (last) {
    const kind = RX.operatorClosed.test(last.text) ? 'operator_closed' : RX.lost.test(last.text) ? 'connection_lost' : RX.callEnded.test(last.text) ? 'call_ended' : 'client_left';
    ev.push({ type: 'end', index: last.index, kind });
  } else {
    const lastOp = [...transcript].reverse().find((m) => m.speaker === 'operator' && !m.on_hold);
    if (lastOp) ev.push({ type: 'end', index: lastOp.index, kind: 'farewell' });
  }
  return ev.sort((a, b) => a.index - b.index);
}

/** Реплики до подключения оператора: что клиент уже успел сказать. */
export function deriveBotContext(transcript) {
  const firstOp = transcript.findIndex((m) => m.speaker === 'operator');
  const upto = firstOp === -1 ? transcript.length : firstOp;
  const before = transcript.slice(0, upto);
  return {
    from: 0,
    to: Math.max(0, upto - 1),
    client: before.filter((m) => m.speaker === 'client' && !/^\d$/.test(m.text.trim())),
    count: before.length,
  };
}

/** Помечает реплики на удержании, если бэкенд не сделал этого сам. */
export function markHold(transcript) {
  let on = false;
  return transcript.map((m) => {
    if (m.speaker === 'system' && RX.holdEnd.test(m.text)) { on = false; return { ...m }; }
    if (m.speaker === 'system' && RX.holdStart.test(m.text)) { on = true; return { ...m }; }
    return { ...m, on_hold: m.on_hold ?? (on && m.speaker !== 'system') };
  });
}

/** Секунды от начала диалога: MM:SS у чатов, ISO у звонков. */
export function withOffsets(transcript) {
  let t0 = null;
  return transcript.map((m) => {
    if (typeof m.offset_sec === 'number') return m;
    let sec = 0;
    const ts = String(m.timestamp || '');
    if (/^\d{1,2}:\d{2}$/.test(ts)) {
      const [mm, ss] = ts.split(':').map(Number);
      sec = mm * 60 + ss;
    } else {
      const ms = Date.parse(ts);
      if (!Number.isNaN(ms)) { if (t0 === null) t0 = ms; sec = Math.round((ms - t0) / 1000); }
    }
    return { ...m, offset_sec: sec };
  });
}

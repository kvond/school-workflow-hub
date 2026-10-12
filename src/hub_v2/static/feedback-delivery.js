"use strict";
const byID = id => document.getElementById(id);
let reviewed = null, sequence = 0, busy = false;
function clearReview() {
  sequence++; reviewed = null; byID('review').replaceChildren();
  byID('attest').checked = false; byID('attest').disabled = true; byID('confirm').disabled = true;
}
async function capture(payload) {
  const response = await fetch('/v2/api/feedback/delivery/capture', {method: 'POST', credentials: 'same-origin',
    cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Delivery was not confirmed.');
  return result;
}
byID('through').onchange = clearReview; byID('handed').onchange = clearReview; byID('handed-packet').onchange = clearReview;
byID('attest').onchange = () => { byID('confirm').disabled = busy || !reviewed || !byID('attest').checked; };
byID('prepare').onclick = async () => {
  if (busy) return;
  clearReview(); const seq = sequence;
  busy = true; byID('prepare').disabled = true; byID('status').textContent = 'Checking exact saved report versions…';
  const request = {action: 'prepare', through: byID('through').value,
    handed_packet_id: byID('handed').checked ? byID('handed-packet').value.trim() : null};
  try {
    if (request.handed_packet_id !== null) {
      if (!/^dailyfeedbackpacket_[a-f0-9]{64}$/.test(request.handed_packet_id)) throw new Error('Enter the exact saved packet reference before recording Handed.');
      const response = await fetch('/v2/api/feedback/delivery/packet?id=' + encodeURIComponent(request.handed_packet_id), {credentials: 'same-origin', cache: 'no-store'});
      const packet = await response.json();
      if (!response.ok || packet.status !== 'ready' || packet.id !== request.handed_packet_id || packet.date !== '2026-10-11' ||
          packet.source_count !== 5 || packet.individual_reports !== 5 || packet.legacy_packets !== 0 || packet.report_counts?.science_writing !== 5)
        throw new Error('The exact five-report Oct 11 science writing packet was not verified.');
      if (seq !== sequence) return;
      byID('packet-status').textContent = `Exact Oct 11 packet: five science writing reports, ${packet.pages} pages. Reference: ${packet.id}`;
    }
    const result = await capture(request);
    if (seq !== sequence) return;
    if (result.schema !== 'hub-feedback-delivery/v1' || result.status !== 'review' ||
        result.through !== request.through || result.handed_packet_id !== request.handed_packet_id ||
        !/^[a-f0-9]{64}$/.test(result.snapshot_sha256) || !Number.isInteger(result.printed_count) ||
        !Number.isInteger(result.handed_count) || !Number.isInteger(result.unknown_count)) throw new Error('The review was not verified.');
    reviewed = {request, result};
    const summary = document.createElement('p');
    summary.textContent = `${result.printed_count} exact reports to record Printed; ${result.handed_count} to record Handed. ${result.unknown_count} unverified versions stay pending.`;
    const fingerprint = document.createElement('p'); fingerprint.textContent = `Review reference: ${result.snapshot_sha256}`;
    byID('review').replaceChildren(summary, fingerprint); byID('attest').disabled = !result.printed_count;
    byID('status').textContent = 'Review the counts, then confirm your printing record.';
  } catch (error) { if (seq === sequence) byID('status').textContent = error.message; }
  finally { busy = false; byID('prepare').disabled = false; }
};
byID('confirm').onclick = async () => {
  if (busy || !reviewed || !byID('attest').checked) return;
  const current = reviewed;
  busy = true; byID('prepare').disabled = true; byID('confirm').disabled = true;
  byID('through').disabled = true; byID('handed').disabled = true;
  byID('handed-packet').disabled = true; byID('attest').disabled = true;
  byID('status').textContent = 'Rechecking the reviewed versions and recording delivery…';
  try {
    const result = await capture({...current.request, action: 'confirm', snapshot_sha256: current.result.snapshot_sha256,
      printed_attested: true, handed_attested: !!current.request.handed_packet_id});
    if (result.schema !== 'hub-feedback-delivery/v1' || result.status !== 'recorded' ||
        result.snapshot_sha256 !== current.result.snapshot_sha256) throw new Error('Delivery readback was not confirmed.');
    clearReview(); byID('status').textContent = `${result.printed_count} exact Printed versions and ${result.handed_count} Handed versions recorded. Saved PDFs remain available to reprint.`;
  } catch (error) {
    clearReview(); byID('status').textContent = error.message + ' No automatic retry was made.';
  } finally { busy = false; byID('prepare').disabled = false;
    byID('through').disabled = false; byID('handed').disabled = false; byID('handed-packet').disabled = false;
  }
};

const panel=document.querySelector('.connection');
const check=document.createElement('button');check.type='button';check.textContent='Check Agenda access';
const status=document.createElement('p');status.className='hint';status.setAttribute('role','status');
status.textContent='Connection check only; lesson data is not loaded.';
panel.append(check,status);
check.addEventListener('click',async()=>{
 check.disabled=true;status.textContent='Checking read-only Agenda access…';
 try{
  const response=await fetch('./api/agenda-connection',{cache:'no-store',credentials:'same-origin',redirect:'error'});
  const result=await response.json();
  if(!response.ok){
   const messages={connection_not_configured:'Backend configuration is still needed.',sign_in_required:'Teacher sign-in could not be verified. Reopen the dashboard and sign in.',runtime_identity_unavailable:'The backend could not use its configured service account.',agenda_read_failed:'The backend could not read the configured Agenda. Check Viewer sharing and the spreadsheet setting.'};
   throw new Error(messages[result.error]||'Connection check unavailable.');
  }
  if(result.schema_version!=='hub-agenda-connection/v1'||result.status!=='metadata_read_verified'||!Number.isInteger(result.tab_count)||result.tab_count<0||!Number.isFinite(Date.parse(result.checked_at))||result.cell_data_loaded!==false||result.save_enabled!==false||result.run_enabled!==false||result.external_writes!==0)throw new Error('Unexpected connection response.');
  panel.querySelector('strong').textContent='Agenda metadata access verified';
  panel.querySelector('span').textContent='Lesson feed, saving and Run are not connected.';
  status.textContent=`Read-only check: ${result.tab_count} tabs · ${new Date(result.checked_at).toLocaleString()}. No lesson cells were loaded.`;
 }catch(error){
  panel.querySelector('strong').textContent='Agenda access not verified';
  panel.querySelector('span').textContent='Run and saving are unavailable.';
  status.textContent=error.message==='Failed to fetch'?'Connection check unavailable.':error.message;
 }finally{check.disabled=false;}
});

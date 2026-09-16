const panel=document.querySelector('.connection');
const rosterStatus=document.querySelector('#roster-sync-status');
const check=document.createElement('button');check.type='button';check.textContent='Check Agenda access';
const runRoster=document.createElement('button');runRoster.type='button';runRoster.textContent='Run roster sync';runRoster.disabled=true;runRoster.title='Needs a verified Infinite Campus reader and roster-workbook writer.';
const status=document.createElement('p');status.className='hint';status.setAttribute('role','status');
let lessonRead=null,accessMessage='',checking=false;
panel.append(check,runRoster,status);
function render(){
 panel.querySelector('strong').textContent=lessonRead?'Agenda lessons loaded':'Agenda ready to load';
 panel.querySelector('span').textContent='Saving and Run remain disabled.';
 const loaded=lessonRead?`Loaded ${lessonRead.classes} classes for ${lessonRead.date} · ${new Date(lessonRead.checkedAt).toLocaleString()}.`:'';
 status.textContent=[loaded,checking?'Checking Agenda access…':accessMessage].filter(Boolean).join(' ');
 status.hidden=!status.textContent;
}
document.addEventListener('agenda-source-loaded',event=>{
 lessonRead=event.detail;accessMessage='';render();
});
check.addEventListener('click',async()=>{
 check.disabled=true;checking=true;render();
 try{
  const response=await fetch('./api/agenda-connection',{cache:'no-store',credentials:'same-origin',redirect:'error'});
  const result=await response.json();
  if(!response.ok){
   const messages={connection_not_configured:'Backend configuration is still needed.',sign_in_required:'Teacher sign-in could not be verified. Reopen the dashboard and sign in.',runtime_identity_unavailable:'The backend could not use its configured service account.',agenda_read_failed:'The backend could not read the configured Agenda. Check Viewer sharing and the spreadsheet setting.'};
   throw new Error(messages[result.error]||'Connection check unavailable.');
  }
  if(result.schema_version!=='hub-agenda-connection/v1'||result.status!=='metadata_read_verified'||!Number.isInteger(result.tab_count)||result.tab_count<0||!Number.isFinite(Date.parse(result.checked_at))||result.cell_data_loaded!==false||result.save_enabled!==false||result.run_enabled!==false||result.external_writes!==0)throw new Error('Unexpected connection response.');
  accessMessage=`Access check passed · ${result.tab_count} tabs · ${new Date(result.checked_at).toLocaleString()}.`;
  if(!lessonRead)accessMessage+=' Choose “Load this date from Agenda” to display lessons.';
 }catch(error){
  accessMessage='Access check failed. '+(error.message==='Failed to fetch'?'Connection check unavailable.':error.message);
 }finally{check.disabled=false;checking=false;render();}
});
render();
async function loadRosterStatus(){
 try{
  const response=await fetch('./api/roster-sync',{cache:'no-store',credentials:'same-origin',redirect:'error'});
  const result=await response.json();
  if(!response.ok||result.schema_version!=='hub-roster-sync/v1'||typeof result.status!=='string'||result.run_enabled!==false||result.external_writes!==0)throw Error();
  const message=`Roster sync: ${result.status} · ${result.next_action?.replaceAll('_',' ')||'review needed'}. Run remains disabled.`;
  rosterStatus.textContent=message;
 }catch{
  const message='Roster sync: status unavailable. It has not run from this dashboard.';
  rosterStatus.textContent=message;
 }
}
loadRosterStatus();

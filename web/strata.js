import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const defaults = {mode:"external",url:"http://127.0.0.1:8080",allow_lifecycle:false,same_gpu:false,
    runtime:"",data_dir:"",port:8082,vision:"gpu",context:32768,timeout_s:1800,cleanup_timeout_s:90,
    min_free_vram_mib:12288,min_free_ram_gib:60};
async function request(path, body) {
    const response = await api.fetchApi(path, body ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)} : {});
    let data;
    try {data=await response.json();} catch {throw new Error(`HTTP ${response.status}: response is not valid JSON`);}
    if (!data || typeof data!=="object") throw new Error(`HTTP ${response.status}: invalid response object`);
    if (!response.ok || data.error) {
        const error=data.error;
        const message=typeof error==="string" ? error : [error?.message,error?.details].filter(value=>typeof value==="string" && value).join(": ");
        const nodes=Object.values(data.node_errors || {}).flatMap(node=>node.errors || []).map(error=>[error.message,error.details].filter(value=>typeof value==="string" && value).join(": ")).filter(Boolean);
        throw new Error([message || `HTTP ${response.status}`, ...nodes].join("\n"));
    }
    return data;
}
function renderPanel(container) {
    container.replaceChildren();
    container.style.cssText="padding:14px;overflow:auto;height:100%;box-sizing:border-box;font-size:13px;line-height:1.5";
    const title=document.createElement("h3"); title.textContent="Strata-T8"; container.append(title);
    const note=document.createElement("p"); note.textContent="连接配置保存在本机。工作流只保存配置名称。托管模式使用独立运行包；每次推理结束后自动释放显存。"; container.append(note);
    const select=document.createElement("select"); const refresh=document.createElement("button"); refresh.textContent="刷新配置";
    select.setAttribute("aria-label","已有配置");
    const name=document.createElement("input"); name.value="default"; name.placeholder="配置名称";
    name.setAttribute("aria-label","配置名称");
    const fields=document.createElement("textarea"); fields.rows=16; fields.style.cssText="width:100%;box-sizing:border-box;font-family:monospace";
    fields.value=JSON.stringify(defaults,null,2);
    fields.setAttribute("aria-label","连接配置 JSON");
    const key=document.createElement("input"); key.type="password"; key.placeholder="API key：留空保留；新托管配置会自动生成"; key.autocomplete="off";
    key.setAttribute("aria-label","API key");
    const clearLabel=document.createElement("label");const clearKey=document.createElement("input");clearKey.type="checkbox";
    clearKey.setAttribute("aria-label","清除已保存 API key");clearLabel.append(clearKey," 清除已保存 API key（托管模式重新生成）");
    const status=document.createElement("pre"); status.style.cssText="white-space:pre-wrap;overflow-wrap:anywhere";
    status.style.fontSize="12px";
    for(const input of [name,fields,key]) input.style.cssText+=";display:block;width:100%;box-sizing:border-box;margin:8px 0;padding:6px;border:1px solid #666;border-radius:4px;background:var(--comfy-input-bg,#333);color:var(--input-text,#eee)";
    let saved={}, initialized=false, reloadGeneration=0, statusGeneration=0;
    const draft=()=>JSON.stringify([name.value,fields.value,key.value,Boolean(clearKey.checked)]);
    let cleanDraft=draft();
    const show=(text,generation)=>{if(generation===statusGeneration)status.textContent=text;};
    const report=(error,generation)=>show(error.message || String(error),generation);
    function selectProfile(invalidate=true){
        if(invalidate)++statusGeneration;
        name.value=select.value;const value={...saved[select.value]};delete value.key_configured;
        fields.value=JSON.stringify(value,null,2);key.value="";clearKey.checked=false;cleanDraft=draft();
    }
    async function reload() {
        const generation=++reloadGeneration, notice=++statusGeneration, before=draft(), clean=before===cleanDraft;
        try { const data=await request("/strata_t8/profiles");if(generation!==reloadGeneration)return;
            if(!data.profiles || typeof data.profiles!=="object" || Array.isArray(data.profiles)) throw new Error("服务未返回有效的配置列表。");
            const unchanged=before===draft();saved=data.profiles; select.replaceChildren();
            for (const profile of Object.keys(saved)) {const option=document.createElement("option");option.value=profile;option.textContent=profile;select.append(option);}
            if(saved[name.value]) select.value=name.value;
            if(clean && unchanged && select.value && (!initialized || saved[name.value])) selectProfile(false);
            initialized=true;
            const errors=Object.keys(data.profile_errors || {}).map(profile=>`配置 ${profile} 无法读取；输入完整配置和替换 API key 后保存。`);
            show([`本机配置目录：${data.home}\n保存后刷新页面，再选择 Strata 连接配置。`,...errors].join("\n"),notice);
        } catch(error) {if(generation===reloadGeneration)report(error,notice);}
    }
    select.onchange=()=>selectProfile();
    refresh.onclick=reload;
    container.append(select,refresh,name,fields,key,clearLabel);
    const save=document.createElement("button"); save.textContent="保存配置";
    const actionButtons=[],activeActions=new Set();let saving=false;
    function updateDisabled(){
        for(const input of [save,refresh,select,name,fields,key,clearKey])input.disabled=saving;
        for(const button of actionButtons)button.disabled=saving || activeActions.has(button);
    }
    save.onclick=async()=>{++reloadGeneration;const notice=++statusGeneration;saving=true;updateDisabled();show("保存中…",notice);try {
        const profile=JSON.parse(fields.value);
        if(!profile || typeof profile!=="object" || Array.isArray(profile)) throw new Error("配置必须为 JSON 对象。");
        await request("/strata_t8/profile",{name:name.value,profile_json:fields.value,api_key:clearKey.checked ? "" : key.value || "__KEEP__"});
        key.value="";clearKey.checked=false;cleanDraft=draft();await reload();
    }catch(error){report(error,notice);}finally{saving=false;updateDisabled();}};
    container.append(save);
    const actions=document.createElement("div");
    actions.style.cssText="display:flex;gap:6px;flex-wrap:wrap;margin:10px 0";
    for(const [action,label] of [["start","启动"],["status","状态"],["load","加载"],["unload","卸载"],["stop","停止托管服务"]]) {
        const button=document.createElement("button");button.textContent=label;
        actionButtons.push(button);
        button.onclick=async()=>{activeActions.add(button);updateDisabled();const notice=++statusGeneration;show("处理中…",notice);try{
            if(action!=="status") {
                const queued=await request("/prompt",{client_id:api.clientId,prompt:{"1":{class_type:"StrataT8Connection",inputs:{profile:name.value}},"2":{class_type:"StrataT8Control",inputs:{connection:["1",0],action}}}});
                if(typeof queued.prompt_id!=="string" || !queued.prompt_id) throw new Error("ComfyUI 未返回队列任务 ID；服务操作未确认入队。");
                show(JSON.stringify(queued,null,2)+"\n服务操作已进入 ComfyUI 队列；同卡加载验证返回前会释放显存。",notice);
            } else show(JSON.stringify(await request("/strata_t8/control",{name:name.value,action}),null,2),notice);
        }catch(error){report(error,notice);}finally{activeActions.delete(button);updateDisabled();}};
        actions.append(button);
    }
    container.append(actions,status); reload();
}
app.registerExtension({name:"Strata-T8.Panel",async setup(){
    if(app.extensionManager?.registerSidebarTab) {
        app.extensionManager.registerSidebarTab({id:"strata-t8",icon:"pi pi-eye",title:"Strata-T8",tooltip:"Strata 连接和服务状态",type:"custom",render:renderPanel});
    } else {
        const button=document.createElement("button");button.textContent="Strata-T8";
        button.onclick=()=>{const dialog=document.createElement("dialog");dialog.style.cssText="width:560px;height:80vh;background:#222;color:#eee";const close=document.createElement("button");close.textContent="关闭";close.onclick=()=>dialog.remove();dialog.append(close);const content=document.createElement("div");dialog.append(content);renderPanel(content);document.body.append(dialog);dialog.showModal();};
        app.ui.menuContainer.append(button);
    }
}});

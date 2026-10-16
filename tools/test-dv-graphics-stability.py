#!/usr/bin/env python3
"""Execute actual graphics-policy receipts and extracted Core2 presenter on host.

Opaque CP acknowledgements are inputs, not a simulated proof of blob correctness.
No kernel/image/module build or device access. Retains the temporary probe oracle.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, resource, subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('probe_fixture', HERE/'dv_graphics_test_fixture.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

SUPPORT = r'''
#define EXPORT_SYMBOL(...)
#define module_param(...) static_assert(true,"module declaration")
static bool dv_new_runtime_failed=false;
static u64 dv_backend_generation(){return fixture_epoch;}
static int kstrtobool(const char*s,bool*out){if(!strcmp(s,"1")||!strcmp(s,"Y")||!strcmp(s,"true")){*out=true;return 0;}if(!strcmp(s,"0")||!strcmp(s,"N")||!strcmp(s,"false")){*out=false;return 0;}return -EINVAL;}
static int param_get_bool(char*,const kernel_param*){return 0;}
'''

TESTS = r'''
static unsigned stable_groups=0;
static void stable_on(){amdv_graphics_capability(true);assert(dv_graphics_stable_set("1",nullptr)==0);assert(dv_graphics_stable&&dv_gfx_stable.engaged);}
static dv_graphics_request request(bool eligible=true){dv_graphics_request r{};amdv_graphics_request(eligible,&r);return r;}
// CP receipt callback, then the exact driver's successful CP acceptance hook.
static void admit(const dm_lut_ipcore& lut,const dv_graphics_request&r,bool mode,bool fresh,u32 flags=0,bool accepted=true,bool newer=true){
 new_dovi_setting=dovi_setting;new_dovi_setting.dm_lut2=lut;
 new_dovi_setting.video_width=3840;new_dovi_setting.video_height=1920;
 dv_graphics_stability_begin();
 if(accepted)amdv_graphics_result(&r,mode,fresh,flags);
 fixture_policy_cp(newer,accepted);
 if(accepted){fixture_trace_cp((int)flags);cp_accept(flags);}
}
static void present(){writes.clear();dma.clear();old_at_entry=dovi_setting.dm_lut2;observing=true;production_frame();observing=false;}
static void turn_on(const dm_lut_ipcore& lut=tagged(1)){
 stable_on();auto r=request();assert(r.enable&&r.retry&&r.tracked);
 admit(lut,r,true,true);present();expect_dma(lut);assert(dv_gfx_stable.applied_valid&&!request().retry);
}
static void assert_unapplied_at_port(){assert(!dv_gfx_stable.applied_valid);assert(dma.size()<=1280);}

static void defaults_and_capability(){
 setup();assert(!dv_graphics_stable&&!dv_graphics_stable_available);assert(dv_graphics_stable_set("invalid",nullptr)==-EINVAL);
 auto r=request();assert(!r.enable&&!r.tracked&&!r.retry);run(tagged(2),0x10);expect_dma(tagged(2));run(tagged(2));assert(dma.empty());
 setup();dv_graphics_stable_set("1",nullptr);r=request();assert(!r.enable&&!r.tracked&&!r.retry&&!dv_graphics_stability_busy());assert(!dv_graphics_stability_changed());
 assert(dv_graphics_probe_set("1",nullptr)==0);dv_graphics_probe_set("0",nullptr);
 amdv_graphics_capability(true);r=request();assert(r.enable&&r.retry&&r.tracked);assert(dv_graphics_stability_busy());
 amdv_graphics_capability(false);r=request();assert(!r.enable&&!r.tracked&&!r.retry&&!dv_graphics_stability_busy());
 assert(!dv_gfx_stable.applied_valid&&!dv_gfx_stable.pending_valid&&dv_graphics_stable);
 setup();stable_on();admit(tagged(2),request(),true,true);assert(dv_graphics_stability_fresh());amdv_graphics_capability(false);present();
 assert(!dv_gfx_stable.applied_valid&&!dv_graphics_stability_fresh()&&!dv_graphics_stability_busy()&&!dv_graphics_stability_changed());
 puts("native default-off, explicit capability advertisement/retirement and unavailable intent PASS");++stable_groups;
}
static void fresh_dma_receipt(){
 setup();stable_on();auto r=request();const auto before=dovi_setting;
 admit(tagged(1),r,true,true);assert(dv_graphics_stability_fresh()&&!dv_gfx_stable.applied_valid&&request().retry);
 fixture_dma_hook=assert_unapplied_at_port;present();fixture_dma_hook=nullptr;
 expect_dma(tagged(1));assert(dv_gfx_stable.applied_valid&&!dv_gfx_stable.pending_fresh&&!request().retry);
 assert(equal(dovi_setting.dm_lut2,tagged(1))&&equal(new_dovi_setting.dm_lut2,tagged(1)));
 assert(!memcmp(&before.dm_reg2,&dovi_setting.dm_reg2,sizeof(before.dm_reg2)));
 assert(!memcmp(&before.dm_reg3,&dovi_setting.dm_reg3,sizeof(before.dm_reg3)));
 assert(!memcmp(&before.md_reg3,&dovi_setting.md_reg3,sizeof(before.md_reg3)));
 const auto gen=dv_graphics_stable_generation;dv_graphics_stable_set("1",nullptr);assert(gen==dv_graphics_stable_generation);
 r=request();admit(tagged(1),r,true,false);present();assert(dma.empty()&&!request().retry);
 puts("fresh equal-byte CP forces full ordered DMA; applied receipt follows every port word; cached CP dedups PASS");++stable_groups;
}
static void cached_mismatch_failure(){
 for(int which=0;which<5;++which){
  setup();stable_on();auto r=request();
  if(which==0)admit(tagged(1),r,true,false);
  if(which==1)admit(tagged(1),r,false,true);
  if(which==2)admit(tagged(1),r,true,true,0,false);
  if(which==3)admit(tagged(1),r,true,true,0,true,false);
  if(which==4){admit(tagged(1),r,true,true);dv_graphics_stability_begin();fixture_policy_cp(true,true);}
  assert(!dv_graphics_stability_fresh()&&!dv_gfx_stable.applied_valid&&request().retry);
  if(which!=2){present();assert(dma.empty()&&!dv_gfx_stable.applied_valid&&request().retry);}
  admit(tagged(1),request(),true,true);present();expect_dma(tagged(1));assert(!request().retry);
 }
 setup();stable_on();auto r=request();admit(tagged(2),r,true,false);present();expect_dma(tagged(2));assert(!dv_gfx_stable.applied_valid&&request().retry);
 puts("cached/mismatched/failed/legacy/missing CP cannot acknowledge intent, even on ordinary changed DMA; fresh retry recovers PASS");++stable_groups;
}
static void const_lifetimes(){
 setup();stable_on();auto r=request();
 admit(tagged(9),r,true,true,CP_FLAG_CONST_TC2|CP_FLAG_CHANGE_TC2);present();assert(dma.empty());
 assert(stb_core2_const_flag&&equal(dovi_setting.dm_lut2,tagged(1))&&!dv_gfx_stable.applied_valid&&request().retry);
 admit(tagged(2),request(),true,false);present();assert(dma.empty()&&stb_core2_const_flag&&request().retry);
 // No TC2 bit: the validated fresh receipt alone retires historical CONST.
 admit(tagged(2),request(),true,true);present();expect_dma(tagged(2));
 assert(!stb_core2_const_flag&&equal(dovi_setting.dm_lut2,tagged(2))&&!request().retry);
 // An ordinary current CONST still retains its old cache after feature use.
 admit(tagged(8),request(),true,true,CP_FLAG_CONST_TC2);present();assert(dma.empty());assert(equal(dovi_setting.dm_lut2,tagged(2)));
 puts("current CONST wins; cached receipt cannot clear it; fresh zero-TC2 receipt retires historical CONST without invalid publication PASS");++stable_groups;
}
static void on_off_and_aba(){
 setup();turn_on();dv_graphics_stable_set("0",nullptr);auto off=request();assert(!off.enable&&off.tracked&&off.retry&&dv_gfx_stable.engaged);
 admit(tagged(1),off,false,false);present();assert(dma.empty()&&dv_gfx_stable.engaged&&request().retry);
 admit(tagged(1),request(),false,true);present();expect_dma(tagged(1));assert(!dv_gfx_stable.engaged&&!request().retry&&!dv_graphics_stability_busy());
 setup();turn_on();dv_graphics_stable_set("0",nullptr);dv_graphics_stable_set("1",nullptr);auto latest=request();assert(latest.retry&&latest.enable);
 admit(tagged(1),latest,true,true);present();expect_dma(tagged(1));assert(!request().retry);
 // A request admitted before on/off/on may finish; its generation is not current.
 setup();stable_on();auto old=request();admit(tagged(2),old,true,true);dv_graphics_stable_set("0",nullptr);dv_graphics_stable_set("1",nullptr);
 present();expect_dma(tagged(2));assert(dv_gfx_stable.applied.generation==old.generation&&request().retry);
 admit(tagged(2),request(),true,true);present();expect_dma(tagged(2));assert(!request().retry);
 puts("off native fresh receipt releases ownership; cached off keeps it; equal-byte ABA and in-flight old generation require current retry PASS");++stable_groups;
}
static void skipped_and_invalidated(){
 for(int which=0;which<2;++which){
  setup();stable_on();auto r=request();admit(tagged(2),r,true,true);
  if(which==0)dolby_vision_mask=5;else dolby_vision_flags|=FLAG_DISABE_CORE_SETTING;
  present();assert(dma.empty()&&!dv_gfx_stable.applied_valid&&request().retry);
  dolby_vision_mask=7;dolby_vision_flags&=~FLAG_DISABE_CORE_SETTING;
  admit(tagged(2),request(),true,true);present();expect_dma(tagged(2));assert(!request().retry);
 }
 for(int which=0;which<3;++which){
  setup();stable_on();admit(tagged(2),request(),true,true);
  if(which==0)++fixture_epoch;else if(which==1)dv_new_runtime_failed=true;else dv_pending_new=false;
  assert(!dv_graphics_stability_fresh());present();assert(!dv_gfx_stable.applied_valid&&request().retry);
 }
 setup();turn_on();enable_dolby_vision(0);assert(dv_graphics_stable&&dv_gfx_stable.engaged&&!dv_gfx_stable.applied_valid&&!dv_gfx_stable.pending_valid);
 setup();turn_on();dv_graphics_stable_set("0",nullptr);enable_dolby_vision(0);assert(!dv_graphics_stable&&!dv_gfx_stable.engaged&&!dv_gfx_stable.applied_valid);
 puts("masked/disabled setter no ACK; epoch/runtime/legacy gates reject stale freshness; hardware disable clears receipts, retains desired intent PASS");++stable_groups;
}
static void paused_retry_and_routes(){
 setup();stable_on();request();assert(dv_graphics_stability_changed());assert(fixture_graphic_changed()==2&&!force_set_lut);
 admit(tagged(1),request(),true,false);present();assert(fixture_graphic_changed()==2&&!force_set_lut);
 admit(tagged(1),request(),true,true);present();assert(fixture_graphic_changed()==0);
 dv_graphics_stable_set("0",nullptr);assert(fixture_graphic_changed()==2&&!force_set_lut);
 admit(tagged(1),request(),false,true);present();assert(fixture_graphic_changed()==0);
 // Desired on + ineligible shim route consumes native once, without a parse loop.
 setup();stable_on();auto r=request(false);assert(!r.enable&&r.retry&&r.tracked);admit(tagged(1),r,false,true);present();expect_dma(tagged(1));
 assert(!request(false).retry&&!dv_graphics_stability_changed()&&fixture_graphic_changed()==0);
 // No on-demand reparse while the route cannot take graphics.
 for(int n=0;n<13;++n){setup();stable_on();request();switch(n){
 case 0:dolby_vision_on=false;break;case 1:is_osd_off=true;break;case 2:dolby_vision_mask=5;break;case 3:dolby_vision_flags|=FLAG_DISABE_CORE_SETTING;break;case 4:dv_pending_new=false;break;case 5:xbmc_dv_source_native=false;break;case 6:xbmc_dv_vp=1;break;case 7:fixture_pq=true;break;case 8:dovi_setting.src_format=FORMAT_SDR;break;case 9:dovi_setting.dst_format=FORMAT_HDR10;break;case 10:dovi_setting.use_ll_flag=1;break;case 11:dovi_setting.g_format=G_HDR_RGB;break;case 12:dovi_setting.g_bitdepth=10;break;}
 assert(!dv_graphics_stability_changed());}
 puts("paused intent requests fresh CP, never cached force; completed ineligible native mode stops retries; route scheduling guards PASS");++stable_groups;
}
static void probe_exclusion(){
 for(int mode=1;mode<=2;++mode){
  setup();stable_on();assert(dv_graphics_probe_set(mode==1?"1":"2",nullptr)==-EBUSY);
  setup();assert(dv_graphics_probe_set(mode==1?"1":"2",nullptr)==0);assert(dv_graphics_stable_set("1",nullptr)==-EBUSY&&!dv_graphics_stable);
 }
 setup();held_B();dv_graphics_probe_set("0",nullptr);assert(dv_graphics_stable_set("1",nullptr)==-EBUSY);run(tagged(2));stable_on();
 setup();turn_on();dv_graphics_stable_set("0",nullptr);assert(dv_graphics_probe_set("1",nullptr)==-EBUSY);admit(tagged(1),request(),false,true);present();assert(dv_graphics_probe_set("1",nullptr)==0);
 setup();arm_hold();fixture_trace_cp(0);new_dovi_setting=dovi_setting;bool upload=false,held=false,restoring=false;
 dv_graphics_probe_select((u32*)&new_dovi_setting.dm_lut2,(u32*)&new_dovi_setting.dm_reg2,0,false,&upload,&held,&restoring);
 assert(dv_gfx_probe.inflight);dv_graphics_probe_set("0",nullptr);assert(dv_graphics_stable_set("1",nullptr)==-EBUSY);
 puts("both temporary probe modes, pending native restoration and in-flight uploads mutually exclude persistent policy PASS");++stable_groups;
}
int main(){defaults_and_capability();fresh_dma_receipt();cached_mismatch_failure();const_lifetimes();on_off_and_aba();skipped_and_invalidated();paused_retry_and_routes();probe_exclusion();probe_tests();printf("%u actual graphics-policy groups PASS plus retained temporary probe groups\n",stable_groups);}
'''

def produce(c, probe, stable, policy):
    saved = p.m.STUBS
    begin = '/* BEGIN default-off graphics stability stubs */'
    end = '/* END default-off graphics stability stubs */'
    a = saved.index(begin)
    b = saved.index(end, a)+len(end)
    p.m.STUBS = saved[:a]+saved[b:]
    try:
        code, parts = p.produce(c, probe+'\n'+SUPPORT+policy+'\n'+stable.replace('#include "dv_graphics_policy.h"',''))
    finally:
        p.m.STUBS = saved
    code = code.replace('static void setup(){', 'static void setup(){\n dv_new_runtime_failed=false;dv_graphics_stable=false;dv_graphics_stable_available=false;dv_graphics_stable_generation=0;memset(&dv_gfx_stable,0,sizeof(dv_gfx_stable));fixture_dma_hook=nullptr;')
    code = code.replace('int main(){controls();', 'static void probe_tests(){controls();')
    # The recorder observes every actual port request before receipt code runs.
    marker = 'static void write_reg(u32 address,u32 value){'
    assert code.count(marker) == 1
    code = code.replace(marker, 'static void(*fixture_dma_hook)()=nullptr;\n'+marker)
    marker = 'if(address==DOLBY_CORE2A_DMA_PORT)dma.push_back(value);'
    assert code.count(marker) == 1
    code = code.replace(marker, 'if(address==DOLBY_CORE2A_DMA_PORT){dma.push_back(value);if(fixture_dma_hook)fixture_dma_hook();}')
    cp_line = 'dv_graphics_stability_cp(use_new, flag >= 0);'
    assert c.count(cp_line) == 1
    code += '\nstatic void fixture_policy_cp(bool use_new,bool accepted){int flag=accepted?0:-1;'+cp_line+'}\n'
    start = c.index('  if (dv_graphics_stability_changed())\n    ret |= 2;')
    tail = c[start:c.index('\n}', start)]
    code += '\nstatic int fixture_graphic_changed(){int ret=0;'+tail+'}\n'+TESTS
    parts['graphics_policy'] = policy
    parts['graphics_stability'] = stable
    parts['graphics_cp_acceptance'] = cp_line
    parts['graphics_reparse_tail'] = tail
    return code, parts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidate', type=Path, default=HERE.parent/'drivers/amlogic/media/enhancement/amdolby_vision')
    ap.add_argument('--root', type=Path, default=HERE.parent, help='Kernel root providing unmodified public/layout/register headers')
    ap.add_argument('--output', type=Path, default=Path('/tmp/dv-graphics-policy-host-tests'))
    ap.add_argument('--negative-controls', action='store_true')
    args = ap.parse_args()
    p.m.ROOT = args.root.resolve()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    args.output.mkdir(parents=True, exist_ok=True)
    files = {n:(args.candidate/n).read_text() for n in ['amdolby_vision.c','dv_graphics_probe.h','dv_graphics_stability.h','dv_graphics_policy.h']}
    code, parts = produce(*files.values())
    results = []

    def run(name, cpp, expect=True, argv=()):
        src, exe = args.output/(name+'.cpp'), args.output/name
        src.write_text(cpp)
        cmd = ['g++','-std=gnu++17','-O0','-g','-Wall','-Wextra','-Werror','-Wno-unused-variable','-Wno-unused-function','-Wno-unused-parameter','-fno-strict-aliasing','-fsanitize=address,undefined','-fno-omit-frame-pointer','-no-pie',str(src),'-o',str(exe)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        (args.output/(name+'-compile.log')).write_text(r.stdout+r.stderr)
        assert r.returncode == 0, (name, r.stderr)
        r = subprocess.run([str(exe),*argv], capture_output=True, text=True, env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'})
        (args.output/(name+'-run.log')).write_text(r.stdout+r.stderr)
        assert (r.returncode == 0) == expect, (name,r.stdout,r.stderr)
        if not expect:
            assert 'Assertion' in r.stderr, (name,r.stderr)
        results.append(dict(name=name,expected_success=expect,returncode=r.returncode,stdout=r.stdout,stderr=r.stderr))
        print(r.stdout if expect else name+': rejected', flush=True)

    run('production', code)
    legacy = code[:code.index('static dm_lut_ipcore tagged(')]+p.m.TESTS
    for backend in ('0','1'):
        for mode in ('lifecycle','adjacent'):
            run('retained-'+backend+'-'+mode, legacy, True, (backend,mode))
    if args.negative_controls:
        mutations = {
            'cached-receipt-is-fresh':('dv_gfx_stable.candidate_fresh = fresh && !(flags & CP_FLAG_CONST_TC2);','dv_gfx_stable.candidate_fresh = !(flags & CP_FLAG_CONST_TC2);'),
            'current-const-is-fresh':('fresh && !(flags & CP_FLAG_CONST_TC2)','fresh'),
            'mode-mismatch-accepted':('request->tracked && mode == request->enable','request->tracked'),
            'failed-cp-keeps-candidate':('dv_gfx_stable.candidate_valid = false;\n}', '/* candidate not cleared */\n}'),
            'no-fresh-equal-byte-force':('if (graphics_fresh)\n    set_lut = true;', 'if (false)\n    set_lut = true;'),
            'historical-const-latch-survives':('if (dv_graphics_stability_fresh())\n      stb_core2_const_flag = false;', 'if (false)\n      stb_core2_const_flag = false;'),
            'missing-post-DMA-receipt':('if (!probe_held && !probe_restoring)\n      dv_graphics_stability_requested();', 'if (!probe_held && !probe_restoring)\n      (void)0;'),
            'ack-before-actual-port-loop':('for (i = 0; i < (256 * 5); i += 4) {\n      VSYNC_WR_DV_REG(DOLBY_CORE2A_DMA_PORT', 'dv_graphics_stability_requested();\n    for (i = 0; i < (256 * 5); i += 4) {\n      VSYNC_WR_DV_REG(DOLBY_CORE2A_DMA_PORT'),
            'ignore-generation-ABA':('request->generation != dv_gfx_stable.applied.generation ||','false ||'),
            'ignore-backend-epoch':('dv_gfx_stable.pending.epoch == dv_backend_generation() &&','true &&'),
            'ignore-runtime-failure':('dv_pending_new && !READ_ONCE(dv_new_runtime_failed);','dv_pending_new;'),
            'hardware-disable-forgets-desired':('static void dv_graphics_stability_disabled(void)\n{','static void dv_graphics_stability_disabled(void)\n{\n dv_graphics_stable=false;'),
            'paused-retry-replaced-with-force':('if (dv_graphics_stability_changed())\n    ret |= 2;', 'if (dv_graphics_stability_changed())\n    force_set_lut = true;'),
            'paused-ineligible-loop':('amdv_graphics_request(READ_ONCE(dv_gfx_stable.eligible), &request);','amdv_graphics_request(true, &request);'),
            'probe-accepts-stable':('return (READ_ONCE(dv_graphics_stable) &&\n\t\tREAD_ONCE(dv_graphics_stable_available)) || READ_ONCE(dv_gfx_stable.engaged);','return false;'),
            'stable-accepts-probe':('if (busy)\n\t\t\treturn -EBUSY;', 'if (busy && false)\n\t\t\treturn -EBUSY;'),
        }
        for name,(old,new) in mutations.items():
            assert code.count(old) == 1, (name, code.count(old))
            run(name, code.replace(old,new), False)
    (args.output/'results.json').write_text(json.dumps(dict(sources={n:hashlib.sha256(t.encode()).hexdigest() for n,t in files.items()},fragments={n:hashlib.sha256(t.encode()).hexdigest() for n,t in parts.items()},results=results),indent=2)+'\n')
    print('Host CP receipt / presenter write-request boundary only; no physical application or opaque CP correctness claim.')

if __name__ == '__main__':
    main()

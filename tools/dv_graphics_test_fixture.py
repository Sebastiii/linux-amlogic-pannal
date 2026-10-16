#!/usr/bin/env python3
"""Compile the actual candidate probe header + extracted production DMA/caller bodies.
Host-only boundary test; no CE, kernel, image, module build or device access.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, resource, subprocess
HERE=Path(__file__).resolve().parent
KERNEL=HERE.parent
spec=importlib.util.spec_from_file_location('lifecycle',KERNEL/'tools/test-dv-core2-lifecycle.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SUPPORT=r'''
#include <algorithm>
#include <cstdarg>
#include <cstddef>
#include <cerrno>
#include <climits>
#include <string>
using std::min;
// Mirror the kernel current macro in extracted production code.
struct task_struct {};
static task_struct* get_current(){return nullptr;}
#define current get_current()
#define READ_ONCE(x) (x)
#define BUILD_BUG_ON(condition) static_assert(!(condition), "production layout guard")
static unsigned int fixture_lock_calls=0;
#define DEFINE_SPINLOCK(x) int x=0
#define spin_lock_irqsave(lock,flags) do{(void)(lock);(flags)=0;++fixture_lock_calls;}while(0)
#define spin_unlock_irqrestore(lock,flags) do{(void)(lock);(void)(flags);}while(0)
#define module_param_cb(...) static_assert(true, "module callback declaration")
#define MODULE_PARM_DESC(...) static_assert(true, "parameter description")
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define PAGE_SIZE 4096
#define time_before(a,b) ((long)((a)-(b))<0)
static unsigned long jiffies=1000;
static unsigned long msecs_to_jiffies(unsigned long x){return x;}
struct kernel_param{};
struct kernel_param_ops{int(*set)(const char*,const kernel_param*)=nullptr;int(*get)(char*,const kernel_param*)=nullptr;};
static int scnprintf(char*b,size_t n,const char*f,...){va_list a;va_start(a,f);int r=vsnprintf(b,n,f,a);va_end(a);return r<0?r:std::min(r,(int)n-1);}
static int kstrtouint(const char*s,unsigned int base,unsigned int*out){char*end;errno=0;if(!s||*s=='-')return -EINVAL;unsigned long n=strtoul(s,&end,base);if(end==s||errno||n>UINT_MAX)return -EINVAL;while(*end=='\n'||*end==' ')++end;if(*end)return -EINVAL;*out=n;return 0;}
static int dv_transaction_busy=0;
static int atomic_cmpxchg(int*p,int old,int val){int was=*p;if(was==old)*p=val;return was;}
static void dv_finish_transaction(){dv_transaction_busy=0;}
static bool fixture_g12b=true,fixture_pq=false,fixture_backend=true;
static u64 fixture_epoch=10;
static void(*backend_hook)()=nullptr;
static bool is_meson_g12b_cpu(){return fixture_g12b;}
static bool graphic_pq_active(){return fixture_pq;}
static bool dv_graphics_probe_backend(u64*epoch,bool){*epoch=fixture_epoch;if(backend_hook){auto h=backend_hook;backend_hook=nullptr;h();}return fixture_backend;}
static bool xbmc_dv_source_native=true,dv_new_blob_enable=true;
static u32 dolby_vision_graphic_min=50,dolby_vision_graphic_max=300,dolby_vision_graphics_priority=0;
static u32 dolby_vision_policy=0,dolby_vision_target_min=1,dolby_vision_target_max[8][8]={{4000}};
static int dv_new_signal_range=-1;
static u32 dv_graphic_blend_test=0,dv_HDR10_graphics_max=0;
static bool fixture_apply_reset=false;
static bool is_osd_off=false;
'''
TESTS=r'''
static dm_lut_ipcore tagged(u32 tag,u32 gamma=77){dm_lut_ipcore x;u32*p=(u32*)&x;for(int i=0;i<1280;++i)p[i]=((i<1024?tag:gamma)<<20)|(u32)i;return x;}
static bool equal(const dm_lut_ipcore&a,const dm_lut_ipcore&b){return !memcmp(&a,&b,sizeof(a));}
static void setup(){
 dv_graphics_probe_disabled();memset(&dv_gfx_probe,0,sizeof(dv_gfx_probe));dv_gfx_probe.reason="idle";
 dv_graphics_probe=0;dv_transaction_busy=0;jiffies=1000;backend_hook=nullptr;
 fixture_g12b=fixture_backend=true;fixture_pq=false;fixture_epoch=10;
 dolby_vision_on=dolby_vision_core1_on=true;dv_pending_new=true;amdv_multi_dv_mode=1;
 force_reset_core2=force_set_lut=stb_core2_const_flag=false;stb_core_setting_update_flag=0;
 force_update_reg=0;dolby_vision_flags=5;dolby_vision_mask=7;dolby_vision_core2_on_cnt=DV_CORE2_RECONFIG_CNT;dolby_vision_on_count=5;
 xbmc_dv_source_native=true;xbmc_dv_vp=0;xbmc_dv_vp_tm=0;dolby_vision_mode=dolby_vision_target_mode=DOLBY_VISION_OUTPUT_MODE_IPT_TUNNEL;dolby_vision_ll_policy=0;
 dolby_vision_graphic_min=50;dolby_vision_graphic_max=300;dolby_vision_graphics_priority=0;osd_graphic_width=1920;osd_graphic_height=1080;
 dolby_vision_policy=0;dolby_vision_target_min=1;dolby_vision_target_max[0][0]=4000;dv_new_signal_range=-1;dv_new_blob_enable=true;dv_graphic_blend_test=dv_HDR10_graphics_max=0;
 display=vinfo_s{};fixture_apply_reset=false;is_osd_off=false;writes.clear();dma.clear();observing=false;
 dovi_setting={};dovi_setting.src_format=dovi_setting.dst_format=FORMAT_DOVI;dovi_setting.g_format=G_SDR_RGB;dovi_setting.g_bitdepth=8;
 dovi_setting.video_width=3840;dovi_setting.video_height=1920;dovi_setting.dm_reg2.vdr_res=(1080U<<16)|1920;
 dovi_setting.dm_lut2=tagged(1);dovi_setting.dm_reg3.d2c_coeff1=0x13572468;dovi_setting.md_reg3.size=5;for(unsigned z=0;z<5;++z)dovi_setting.md_reg3.raw_metadata[z]=0xabcdef00+z;dovi_setting.vsvdb_len=3;dovi_setting.vsvdb_tbl[0]=0x46;dovi_setting.vsvdb_tbl[1]=0xd0;dovi_setting.vsvdb_tbl[2]=0;new_dovi_setting=dovi_setting;
}
static void run(const dm_lut_ipcore&curve,u32 flags=0){
 new_dovi_setting=dovi_setting;new_dovi_setting.dm_lut2=curve;new_dovi_setting.video_width=3840;new_dovi_setting.video_height=1920;
 fixture_trace_cp((int)flags);cp_accept(flags);writes.clear();dma.clear();old_at_entry=dovi_setting.dm_lut2;observing=true;production_frame();observing=false;
}
static void expect_dma(const dm_lut_ipcore&x){assert(dma.size()==1280);const u32*p=(const u32*)&x;for(size_t i=0;i<dma.size();++i)assert(dma[i]==p[(i&~3U)+(3-(i&3U))]);}
static void arm_hold(){assert(dv_graphics_probe_set("2",nullptr)==0);assert(dv_graphics_probe==2);}
static void held_B(){arm_hold();run(tagged(1));expect_dma(tagged(1));assert(dv_gfx_probe.restore_pending);run(tagged(2),0x10);expect_dma(tagged(1));assert(equal(dovi_setting.dm_lut2,tagged(2)));}
static int groups=0;
static void controls(){
 setup();const u32 fingerprint_words[]={0x01020304U,0xaabbccddU};assert(dv_graphics_probe_hash(fingerprint_words,2)==0xbd2bd07dU);assert(dv_graphics_probe==0);assert(dv_graphics_probe_set("3",nullptr)==-EINVAL);assert(dv_graphics_probe_set("-1",nullptr)==-EINVAL);assert(dv_graphics_probe_set("2junk",nullptr)==-EINVAL);
 dv_transaction_busy=1;assert(dv_graphics_probe_set("2",nullptr)==-EBUSY);assert(dv_graphics_probe==0);dv_transaction_busy=0;
 arm_hold();auto expiry=dv_gfx_probe.expires;auto token=dv_gfx_probe.token;jiffies+=100;assert(dv_graphics_probe_set("2",nullptr)==-EBUSY);assert(dv_gfx_probe.expires==expiry&&dv_gfx_probe.token==token);
 dv_transaction_busy=1;assert(dv_graphics_probe_set("0",nullptr)==0);assert(dv_graphics_probe==0);dv_transaction_busy=0;
 setup();fixture_g12b=false;assert(dv_graphics_probe_set("2",nullptr)==-EINVAL);
 setup();fixture_backend=false;assert(dv_graphics_probe_set("2",nullptr)==-EINVAL);
 setup();fixture_pq=true;assert(dv_graphics_probe_set("2",nullptr)==-EINVAL);
 setup();stb_core2_const_flag=true;assert(dv_graphics_probe_set("2",nullptr)==-EINVAL);
 setup();force_set_lut=true;assert(dv_graphics_probe_set("2",nullptr)==-EINVAL);
 setup();new_dovi_setting=dovi_setting;assert(dv_graphics_probe_hash((u32*)&dovi_setting.dm_lut2,1280)!=0);
 puts("control lease/EBUSY/nonrenewal/baseline admission PASS");++groups;
}
static void off_trace(){
 setup();bool upload=false,held=true,restoring=true;auto*normal=(u32*)&new_dovi_setting.dm_lut2;auto locks=fixture_lock_calls;assert(dv_graphics_probe_select(normal,(u32*)&new_dovi_setting.dm_reg2,0,false,&upload,&held,&restoring)==normal);assert(!held&&!restoring&&!upload&&fixture_lock_calls==locks);
 run(tagged(2),0x10);expect_dma(tagged(2));assert(equal(dovi_setting.dm_lut2,tagged(2)));assert(!dv_gfx_probe.restore_pending);
 run(tagged(2));assert(dma.empty());
 setup();assert(dv_graphics_probe_set("1",nullptr)==0);run(tagged(2),0x10);expect_dma(tagged(2));assert(!dv_gfx_probe.restore_pending&&dv_gfx_probe.held==0);
 puts("off and trace-only DMA/cache equivalence PASS");++groups;
}
static void holding(){
 setup();const auto baseline=dovi_setting;arm_hold();assert(!memcmp(dv_gfx_probe.baseline,&baseline.dm_lut2,4096));run(tagged(1));expect_dma(tagged(1));assert(dv_gfx_probe.held==1);assert(equal(dovi_setting.dm_lut2,tagged(1)));
 run(tagged(2),0x10);expect_dma(tagged(1));assert(equal(dovi_setting.dm_lut2,tagged(2)));assert(equal(new_dovi_setting.dm_lut2,tagged(2)));assert(!memcmp(&dovi_setting.dm_reg2,&baseline.dm_reg2,sizeof(baseline.dm_reg2)));assert(!memcmp(&dovi_setting.dm_reg3,&baseline.dm_reg3,sizeof(baseline.dm_reg3)));assert(!memcmp(&dovi_setting.md_reg3,&baseline.md_reg3,sizeof(baseline.md_reg3)));
 run(tagged(2));assert(dma.empty());assert(dv_gfx_probe.restore_pending);assert(dv_gfx_probe.held==2);
 assert(dv_graphics_probe_set("0",nullptr)==0);assert(dv_gfx_probe.restore_pending);run(tagged(2));expect_dma(tagged(2));assert(!dv_gfx_probe.restore_pending&&dv_gfx_probe.restored==1);
 run(tagged(2));assert(dma.empty());
 puts("first hold after dedup, four-table scratch, normal cache, disarm restore PASS");++groups;
}
static void invalidations(){
 for(int which=0;which<13;++which){
  setup();held_B();u32 flags=0;auto x=tagged(2);
  switch(which){case 0:jiffies=dv_gfx_probe.expires;break;case 1:flags=CP_FLAG_CHANGE_TC2;break;case 2:flags=CP_FLAG_CONST_TC2|CP_FLAG_CHANGE_TC2;x=tagged(9);break;case 3:force_reset_core2=true;break;case 4:dovi_setting.dm_reg2.s_range++;break;case 5:dovi_setting.dm_reg3.d2c_coeff1++;break;case 6:x=tagged(2,88);break;case 7:dolby_vision_graphic_max++;break;case 8:fixture_epoch++;break;case 9:force_set_lut=true;break;case 10:force_update_reg=0x10;break;case 11:fixture_apply_reset=true;break;case 12:dovi_setting.vsvdb_len=1;dovi_setting.vsvdb_tbl[0]=0x99;break;}
  run(x,flags);auto expected=which==2?tagged(2):x;expect_dma(expected);assert(dv_graphics_probe==0);assert(!dv_gfx_probe.restore_pending);assert(dv_gfx_probe.restored==1);
 }
 puts("expiry/TC2/CONST/reset/DM2/DM3/g2l/config/epoch normal restoration PASS");++groups;
}
static void skips(){
 setup();held_B();dv_graphics_probe_set("0",nullptr);dolby_vision_flags|=FLAG_DISABE_CORE_SETTING;run(tagged(2));assert(dma.empty());assert(dv_gfx_probe.restore_pending);assert(dv_graphics_probe_set("2",nullptr)==-EBUSY);dolby_vision_flags&=~FLAG_DISABE_CORE_SETTING;run(tagged(2));expect_dma(tagged(2));assert(!dv_gfx_probe.restore_pending);
 setup();held_B();dolby_vision_mask=5;run(tagged(2));assert(dma.empty());assert(dv_gfx_probe.restore_pending&&dv_graphics_probe==0);assert(dv_graphics_probe_set("2",nullptr)==-EBUSY);dolby_vision_mask=7;run(tagged(2));expect_dma(tagged(2));assert(!dv_gfx_probe.restore_pending);
 setup();held_B();enable_dolby_vision(0);assert(!dv_gfx_probe.restore_pending);assert(dv_graphics_probe==0);
 puts("skip/mask preserve restoration, rearm rejection, hardware disable clear PASS");++groups;
}
static void gamma_protection(){
 setup();held_B();run(tagged(3,88),0x10);expect_dma(tagged(3,88));assert(dv_graphics_probe==0);assert(!dv_gfx_probe.restore_pending);
 puts("changed g2l is normal restoration and never baseline gamma PASS");++groups;
}

static void all_guards(){
 for(int which=0;which<31;++which){setup();switch(which){
 case 0:fixture_g12b=false;break;case 1:fixture_backend=false;break;case 2:dolby_vision_on=false;break;case 3:dolby_vision_core1_on=false;break;
 case 4:xbmc_dv_source_native=false;break;case 5:xbmc_dv_vp=1;break;case 6:fixture_pq=true;break;case 7:is_osd_off=true;break;
 case 8:osd_graphic_width=0;break;case 9:osd_graphic_height=0;break;case 10:dolby_vision_graphic_max=0;break;case 11:dolby_vision_graphics_priority=1;break;
 case 12:dolby_vision_flags|=FLAG_USE_SINK_MIN_MAX;break;case 13:dolby_vision_flags|=FLAG_PRIORITY_GRAPHIC;break;case 14:dovi_setting.vsvdb_len=33;break;
 case 15:dolby_vision_mode=DOLBY_VISION_OUTPUT_MODE_HDR10;break;case 16:dolby_vision_target_mode=DOLBY_VISION_OUTPUT_MODE_HDR10;break;case 17:dolby_vision_ll_policy=1;break;
 case 18:dovi_setting.src_format=FORMAT_SDR;break;case 19:dovi_setting.dst_format=FORMAT_HDR10;break;case 20:dovi_setting.dovi_ll_enable=1;break;
 case 21:dovi_setting.use_ll_flag=1;break;case 22:dovi_setting.g_format=G_HDR_RGB;break;case 23:dovi_setting.g_bitdepth=10;break;case 24:dolby_vision_mask=5;break;
 case 25:force_update_reg=0x10;break;case 26:dolby_vision_flags|=FLAG_MUTE;break;case 27:dolby_vision_core2_on_cnt=0;break;case 28:force_reset_core2=true;break;case 29:dovi_setting.diagnostic_enable=1;break;case 30:dovi_setting.diagnostic_mux_select=1;break;}
 assert(dv_graphics_probe_set("2",nullptr)==-EINVAL);assert(dv_graphics_probe==0);assert(!dv_transaction_busy);
 }
 setup();arm_hold();for(int part=0;part<22;++part){setup();held_B();dv_gfx_probe.key.config[part]^=1;run(tagged(2));expect_dma(tagged(2));assert(dv_graphics_probe==0&&!dv_gfx_probe.restore_pending);}
 setup();held_B();dovi_setting.vsvdb_tbl[0]^=1;run(tagged(2));expect_dma(tagged(2));assert(dv_graphics_probe==0&&!dv_gfx_probe.restore_pending);
 setup();held_B();dv_gfx_probe.key.epoch++;run(tagged(2));expect_dma(tagged(2));assert(dv_graphics_probe==0);
 puts("31 arm eligibility guards + all 22 config-key fields + VSVDB bytes/epoch PASS");++groups;
}
static void status_and_deadline(){
 setup();arm_hold();char status[PAGE_SIZE];dv_graphics_probe_status(status,nullptr);assert(strstr(status,"requested=2"));assert(strstr(status,"mode=2"));
 run(tagged(1));assert(dv_gfx_probe.restore_pending);jiffies=dv_gfx_probe.expires;assert(!dv_graphics_probe_live());assert(dv_graphics_probe_tracing());
 dv_graphics_probe_get(status,nullptr);assert(!strcmp(status,"0\n"));dv_graphics_probe_status(status,nullptr);assert(strstr(status,"requested=2"));assert(strstr(status,"mode=0"));
 jiffies=dv_gfx_probe.expires+2000;assert(!dv_graphics_probe_tracing());assert(dv_gfx_probe.restore_pending);run(tagged(1));expect_dma(tagged(1));assert(!dv_gfx_probe.restore_pending);
 setup();jiffies=ULONG_MAX-4000;arm_hold();jiffies+=7999;assert(dv_graphics_probe_live());++jiffies;assert(!dv_graphics_probe_live());
 puts("status requested/live distinction, bounded closing trace, jiffies wrap PASS");++groups;
}



static void fresh_cp_association(){
 setup();assert(dv_graphics_probe_set("1",nullptr)==0);frame_count=100;fixture_trace_cp(0);auto old_cp=dv_gfx_probe.pending_cp;auto old_token=dv_gfx_probe.token;assert(old_cp>0);
 dv_graphics_probe_set("0",nullptr);arm_hold();assert(dv_gfx_probe.token>old_token);assert(dv_gfx_probe.pending_cp==0&&dv_gfx_probe.pending_frame==0);
 new_dovi_setting=dovi_setting;bool upload=false,held=false,restoring=false;u32*normal=(u32*)&new_dovi_setting.dm_lut2;
 auto*selected=dv_graphics_probe_select(normal,(u32*)&new_dovi_setting.dm_reg2,0,false,&upload,&held,&restoring);
 assert(selected==normal&&!held&&!restoring&&!upload);assert(dv_graphics_probe==2&&!dv_gfx_probe.inflight&&!dv_gfx_probe.restore_pending);
 frame_count=101;fixture_trace_cp(0);assert(dv_gfx_probe.pending_cp>old_cp&&dv_gfx_probe.pending_frame==101);
 selected=dv_graphics_probe_select(normal,(u32*)&new_dovi_setting.dm_reg2,0,false,&upload,&held,&restoring);
 assert(selected!=normal&&held&&upload&&!restoring);dv_graphics_probe_requested(held,restoring);
 dv_graphics_probe_set("0",nullptr);run(tagged(1));expect_dma(tagged(1));assert(!dv_gfx_probe.restore_pending);
 puts("new arm clears prior trace receipt; retained cp0 waits; fresh same-arm CP admits hold PASS");++groups;
}

static void inflight_cleanup(){
 setup();arm_hold();new_dovi_setting=dovi_setting;fixture_trace_cp(0);
 bool upload=false,held=false,restoring=false;
 auto *normal=(u32*)&new_dovi_setting.dm_lut2;
 auto *selected=dv_graphics_probe_select(normal,(u32*)&new_dovi_setting.dm_reg2,0,false,&upload,&held,&restoring);
 assert(selected!=normal&&held&&upload&&!restoring);assert(dv_gfx_probe.inflight&&!dv_gfx_probe.restore_pending);
 assert(dv_graphics_probe_set("0",nullptr)==0);
 char status[PAGE_SIZE];dv_graphics_probe_status(status,nullptr);
 assert(strstr(status,"mode=0")&&strstr(status,"restore_pending=0")&&strstr(status,"inflight=1"));
 assert(dv_graphics_probe_set("2",nullptr)==-EBUSY);
 dv_graphics_probe_requested(held,restoring);
 dv_graphics_probe_status(status,nullptr);assert(strstr(status,"restore_pending=1")&&strstr(status,"inflight=0"));
 run(tagged(1));expect_dma(tagged(1));assert(!dv_gfx_probe.inflight&&!dv_gfx_probe.restore_pending);
 puts("select-held -> cancel -> status -> requested -> normal restoration retains ownership PASS");++groups;
}

static void cancel_hook(){dv_graphics_probe_set("0",nullptr);}
static void expiry_hook(){jiffies=dv_gfx_probe.expires;}

static void race_observations(){
 for(int kind=0;kind<2;++kind){setup();held_B();backend_hook=kind?expiry_hook:cancel_hook;run(tagged(3),0x10);expect_dma(tagged(3));assert(!dv_gfx_probe.restore_pending);printf("%s during key check rejects hold and requests normal restoration: PASS\n",kind?"expiry":"cancel");run(tagged(3));assert(dma.empty());}
}
int main(){controls();all_guards();off_trace();holding();invalidations();skips();gamma_protection();status_and_deadline();fresh_cp_association();inflight_cleanup();race_observations();printf("%d actual-production host groups PASS; two cancel/expiry guard races rejected\n",groups);}
'''

def produce(text,header):
    saved_stubs=m.STUBS
    if '/* BEGIN default-off graphics probe stubs */' in m.STUBS:
        begin=m.STUBS.index('/* BEGIN default-off graphics probe stubs */')
        end=m.STUBS.index('/* END default-off graphics probe stubs */',begin)+len('/* END default-off graphics probe stubs */')
        m.STUBS=m.STUBS[:begin]+m.STUBS[end:]
    try:
        code,parts=m.generate(text)
    finally:
        m.STUBS=saved_stubs
    support=SUPPORT+header.replace('#include <linux/jiffies.h>','')+'\n'
    code=code.replace(parts['setter'],support+parts['setter'],1)
    cp_start=text.index('\t\tif (flag >= 0)\n\t\t\tdv_gfx_probe.pending_cp = 0;')
    cp_end=text.index('\n\t\t// Copy original source metadata',cp_start)
    cp_admission=text[cp_start:cp_end]
    receipt='\nstatic void fixture_trace_cp(int flag){u64 graphics_trace_cp=0;const bool use_new=dv_pending_new;const u64 backend_epoch=fixture_epoch;\n'+cp_admission+'\n}\n'
    code=code.replace(m.TESTS,receipt+TESTS)
    parts['cp_trace_admission']=cp_admission
    code=code.replace('const bool reset_flag=false;','const bool reset_flag=fixture_apply_reset;')
    return code,parts

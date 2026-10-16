#!/usr/bin/env python3
"""Compile the actual shim callbacks with host Linux-service mocks.
Host tests establish adapter contracts, not target ABI or concurrency.
Graphics request/result services are driver mocks; presenter policy is tested separately.
"""
from pathlib import Path
import subprocess,re,os,argparse,tempfile
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
ap.add_argument('--driver-dir',type=Path,help='Optional staged driver source directory')
a=ap.parse_args();root=a.source_root.resolve()
p=a.driver_dir.resolve() if a.driver_dir else root/'drivers/amlogic/media/enhancement/amdolby_vision'
public=(root/'include/linux/amlogic/media/amdolbyvision/dolby_vision.h').read_text()
public=public[:public.index('void enable_dolby_vision')]
public=re.sub(r'^#include.*\n','',public,flags=re.M)+ '\n#endif\n'
private=(p/'amdolby_vision.h').read_text()
private=re.sub(r'^#include.*\n','',private,flags=re.M)
abi=(p/'dv5_compat_abi.h').read_text()
abi=re.sub(r'^#include.*\n','',abi,flags=re.M)
policy=(p/'dv_graphics_policy.h').read_text()
policy=re.sub(r'^#include.*\n','',policy,flags=re.M)
source=(p/'dv_compat_shim.c').read_text()
source=re.sub(r'^#include.*\n','',source,flags=re.M)
# Actual AArch64 profile validation is exercised separately. Registration uses
# host callable mocks after that service boundary, because AArch64 jump entries
# cannot execute in an x86 harness.
source=source.replace('static int validate_blob(', 'static int validate_blob_real(')
source=source.replace('int register_dv5shim_func(', """
static bool mock_profile_registration;
static int validate_blob(const struct dv5_funcs *f,struct module **owner,unsigned long *base) {
 if (!mock_profile_registration) return validate_blob_real(f,owner,base);
 if (!f || !f->multi_control_path || !f->multi_mp_init || !f->multi_mp_reset ||
     !f->multi_mp_process || !f->multi_mp_release) return -EINVAL;
 *owner=&fake_owner;*base=0x100000;return 0;
}
int register_dv5shim_func(""")
preamble=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef int16_t s16; typedef int32_t s32; typedef uint32_t __le32;
typedef unsigned int uint;
struct vframe_s;
enum vpu_mod_e { HOST_VPU=0 }; enum vd_path_e { HOST_VD=0 };
#define __aligned(x) __attribute__((aligned(x)))
#define __noreturn __attribute__((noreturn))
#define __init
#define __exit
#define BIT(n) (1U<<(n))
#define READ_ONCE(x) (x)
#define unlikely(x) (x)
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define BUILD_BUG_ON(x) _Static_assert(!(x), "layout mismatch")
#define module_param(...)
#define MODULE_PARM_DESC(...)
#define EXPORT_SYMBOL(...)
#define MODULE_LICENSE(...)
#define MODULE_DESCRIPTION(...)
#define module_init(...)
#define module_exit(...)
#define DEFINE_MUTEX(name) int name
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define HZ 100
#define DEFINE_RATELIMIT_STATE(name,a,b) int name
#define __ratelimit(p) (1)
#define pr_info(...) host_log(__VA_ARGS__)
#define le32_to_cpu(x) (x)
#define preempt_disable() ((void)0)
#define preempt_enable() ((void)0)
#define scnprintf snprintf
struct module {
 char name[64];
 struct { void *base; unsigned int text_size; } core_layout;
 bool live;
 int refs;
};
static struct module fake_owner;
static bool owner_lookup_enabled=true;
static struct module *__module_address(unsigned long p) {
 unsigned long b=(unsigned long)fake_owner.core_layout.base;
 return owner_lookup_enabled && p>=b && p<b+fake_owner.core_layout.text_size ? &fake_owner:NULL;
}
static bool try_module_get(struct module *m) { if (!m || !m->live) return false; m->refs++; return true; }
static void module_put(struct module *m) { assert(m->refs>0); m->refs--; }
static void host_log(const char *fmt, ...) { (void)fmt; }
static int vprintk(const char *fmt,va_list ap) { (void)fmt; (void)ap; return 0; }
static void get_random_bytes(void *p,size_t n) { memset(p,0xa5,n); }
static __noreturn void host_panic(const char *fmt, ...) { (void)fmt; abort(); }
#define panic(...) host_panic(__VA_ARGS__)
'''
services=r'''

/* Driver service boundary: capture requests/receipts without substituting
 * shim eligibility, ACK parsing, flag conversion or registration logic. */
static bool host_graphics_capable,host_graphics_desired,host_graphics_tracked;
static unsigned host_capability_calls,host_request_calls,host_result_calls;
static bool host_request_eligible,host_result_mode,host_result_fresh;
static int host_result_flags;
static struct dv_graphics_request host_request,host_result;
void amdv_graphics_capability(bool available) {
 host_graphics_capable=available;host_capability_calls++;
}
void amdv_graphics_request(bool eligible,struct dv_graphics_request *request) {
 host_request_calls++;host_request_eligible=eligible;
 *request=(struct dv_graphics_request){.epoch=0x12345678,.generation=17,
  .enable=eligible && host_graphics_desired && host_graphics_capable,
  .retry=host_graphics_tracked,.tracked=host_graphics_tracked && host_graphics_capable};
 host_request=*request;
}
void amdv_graphics_result(const struct dv_graphics_request *request,bool mode,bool fresh,int flags) {
 host_result_calls++;host_result=*request;host_result_mode=mode;
 host_result_fresh=fresh;host_result_flags=flags;
}
static int driver_reg_ret,driver_unreg_ret;
static unsigned l11_count,l11_clear;
static struct dolby_vision_func_s *registered;
int register_dv_functions_multi(const struct dolby_vision_func_s *f) {
 if (!driver_reg_ret) registered=(struct dolby_vision_func_s *)f;
 return driver_reg_ret;
}
int unregister_dv_functions_multi(void) {
 if (!driver_unreg_ret) registered=NULL;
 return driver_unreg_ret;
}
void amdv_set_l11(const struct dv5_vsif_parameter_s *v,const struct dv5_content_info_s *c) {
 if (v && c) l11_count++; else l11_clear++;
}
'''
tests=r'''
static int cp_ret,cp_bad;
static u32 cp_ack_magic,cp_ack_flags,cp_request[5];
static bool cp_flip_preference;
static unsigned cp_calls,cp_resets;
static int fake_cp(struct m_dovi_setting_s *m) {
 if (!m->num_input) {cp_resets++;return -1;}
 cp_calls++;
 memcpy(cp_request,m->reserved+DV_GFX_REQUEST_SLOT,sizeof(cp_request));
 if(cp_flip_preference)host_graphics_desired=false;
 m->reserved[DV_GFX_ACK_SLOT]=cp_ack_magic;
 m->reserved[DV_GFX_FLAGS_SLOT]=cp_ack_flags;
 m->dm_reg1; /* removed below; keep mocking the real newer nested field */
 m->core1[0].dm_reg.s_range=0xfeed;
 m->dm_reg3.d2c_coeff1=0xbeef;
 m->md_reg3.size=24;
 m->output_vsif.l11_md_present=1;
 m->content_info.content_type_info=3;
 if (cp_bad==1) m->output_ctrl_data_len=4097;
 if (cp_bad==2) m->vsvdb_len=33;
 if (cp_bad==3) m->md_reg3.size=513;
 if (cp_bad==4) m->output_ctrl_data=NULL;
 if (cp_bad==5) m->dovi_ll_enable=2;
 return cp_ret;
}
static unsigned init_calls,release_calls,reset_calls,process_calls;
static bool init_fail;
static int reset_ret;
static int process_ret,process_bad;
static int context_token;
static void *fake_init(int f) { (void)f;init_calls++;return init_fail ? NULL:&context_token; }
static int fake_reset(void *ctx,int f) { assert(ctx==&context_token);(void)f;reset_calls++;return reset_ret; }
static void fake_release(void **ctx) { assert(*ctx==&context_token);release_calls++;*ctx=NULL; }
static int fake_process(void *ctx,char *src,int len,char *comp,int *cl,char *md,int *ml,bool eos,int ty) {
 assert(ctx==&context_token);assert(src && len>=7);(void)eos;(void)ty;
 process_calls++;memcpy(comp,"cmp!",4);memcpy(md,"meta!",5);*cl=4;*ml=5;
 if (process_bad==1) *cl=32785;
 if (process_bad==2) *ml=4097;
 if (process_bad==3) *cl=-1;
 if (process_bad==4) *ml=-1;
 return process_ret;
}
static struct dv5_funcs mock_funcs;
static void setup_mock(void) {
 memset(&mock_funcs,0,sizeof(mock_funcs));
 mock_funcs.version_info="chip_name = g12b [stb:2.6:e]-[v1.0]-";
 mock_funcs.multi_control_path=fake_cp;mock_funcs.multi_mp_init=fake_init;
 mock_funcs.multi_mp_reset=fake_reset;mock_funcs.multi_mp_process=fake_process;
 mock_funcs.multi_mp_release=fake_release;
 blob_funcs=&mock_funcs;blob_owner=&fake_owner;fake_owner.live=true;
 fake_owner.refs=0;mp_ctx=&context_token;mp_attached=false;mp_ready=true;
 cp_forget();cp_setup_invalid();
 blob_graphics_reference=false;host_graphics_capable=false;
 host_graphics_desired=host_graphics_tracked=false;
 host_capability_calls=host_request_calls=host_result_calls=0;
 cp_ack_magic=cp_ack_flags=0;cp_flip_preference=false;memset(cp_request,0,sizeof(cp_request));
}
static int call_cp(struct dovi_setting_s *s) {
 char comp[4]={0},md[8]={0};struct hdr10_parameter hdr={0};
 return cp_adapter(FORMAT_DOVI,FORMAT_DOVI,comp,4,md,8,V_PRIORITY,10,0,0,0,100,0,1000,1,&hdr,s);
}
static void test_cp(void) {
 struct dovi_setting_s s={0},before;
 unsigned n,l;
 setup_mock();mp_attached=true;s.video_width=1920U<<16;s.video_height=1080U<<16;s.g_bitdepth=8;
 cp_ret=0x100|0x200|0x200000;
 assert(call_cp(&s)==(0x10|0x20|0x200000));assert(s.dm_reg1.s_range==0xfeed);
 assert(s.dm_reg3.d2c_coeff1==0xbeef);assert(fake_owner.refs==0);
 assert(shim_m_setting.input[0].set_bit_depth==12);assert(cp_resets==1);
 assert(call_cp(&s)==(0x10|0x20|0x200000));assert(cp_resets==1);
 before=s;l=l11_count;cp_ret=-2;assert(call_cp(&s)==-2);
 assert(!memcmp(&s,&before,sizeof(s)));assert(l11_count==l);assert(fake_owner.refs==0);
 cp_ret=0;
 for(cp_bad=1;cp_bad<=5;cp_bad++) {
  assert(call_cp(&s)==-EOVERFLOW);assert(!memcmp(&s,&before,sizeof(s)));assert(l11_count==l);
 }
 cp_bad=0;n=cp_calls;s.vsvdb_len=33;before=s;
 assert(call_cp(&s)==-EINVAL);assert(cp_calls==n);assert(!memcmp(&s,&before,sizeof(s)));
 s.vsvdb_len=0;fake_owner.live=false;
 assert(call_cp(&s)==-ENODEV);assert(cp_calls==n);assert(fake_owner.refs==0);
 fake_owner.live=true;s.video_width=0xffffU<<16;
 assert(call_cp(&s)==-EINVAL);
 cp_forget();
 assert(cp_adapter(FORMAT_INVALID,FORMAT_DOVI,NULL,0,NULL,0,V_PRIORITY,0,0,0,0,0,0,0,1,NULL,&s)==-1);
 assert(last_cp.in==FORMAT_INVALID);assert(fake_owner.refs==0);
}
static void test_mp(void) {
 static char comp[32784],md[4096];char src[8]={0};int cl=77,ml=88;unsigned n;
 setup_mock();assert(mp_reset_adapter(1)==-ENODEV);
 assert(mp_init_adapter(1)==&context_token);assert(init_calls==0);
 assert(mp_init_adapter(1)==&context_token);assert(init_calls==0);
 assert(mp_reset_adapter(1)==0);assert(reset_calls==1);
 assert(mp_reset_adapter(0)==0);assert(reset_calls==1);
 memset(comp,0x55,sizeof(comp));memset(md,0x66,sizeof(md));
 n=process_calls;assert(mp_process_adapter(src,6,comp,&cl,md,&ml,true)==-EINVAL);
 assert(process_calls==n);assert(cl==77 && ml==88);
 process_ret=-1;
 assert(mp_process_adapter(src,8,comp,&cl,md,&ml,true)==-1);
 assert(cl==77 && ml==88 && comp[0]==0x55 && md[0]==0x66);
 process_ret=0;
 for(process_bad=1;process_bad<=4;process_bad++) {
  assert(mp_process_adapter(src,8,comp,&cl,md,&ml,true)==-EOVERFLOW);
  assert(cl==77 && ml==88 && comp[0]==0x55 && md[0]==0x66);
 }
 process_bad=0;process_ret=1;
 assert(mp_process_adapter(src,8,comp,&cl,md,&ml,true)==1);
 assert(cl==4 && ml==5);assert(!memcmp(comp,"cmp!",4) && !memcmp(md,"meta!",5));
 mp_release_adapter();assert(mp_ctx==&context_token && !mp_attached && mp_ready);
 assert(release_calls==0 && fake_owner.refs==0 && reset_calls==2);
 assert(mp_process_adapter(src,8,comp,&cl,md,&ml,true)==-ENODEV);
 mp_release_adapter();assert(release_calls==0 && reset_calls==2);
 assert(mp_init_adapter(0)==&context_token);assert(init_calls==0);
 reset_ret=-1;mp_release_adapter();assert(!mp_attached && !mp_ready && mp_ctx);
 assert(mp_init_adapter(0)==NULL);assert(fake_owner.refs==0);
 reset_ret=0;fake_owner.live=false;
 /* module_exit still owns module text; driver has quiesced callbacks */
 mp_release_owned();assert(mp_ctx==NULL && release_calls==1);
}
static u8 profile_text[0x29000] __aligned(4096);
static struct dv5_funcs profile_funcs;
static void setup_profile(void) {
 unsigned long b=(unsigned long)profile_text;
 memset(profile_text,0,sizeof(profile_text));memset(&profile_funcs,0,sizeof(profile_funcs));
 *(u32*)profile_text=0xd2848888U;
 unsigned long st[]={0x1d0,0x1d4,0x1e4,0x1d8,0x1e0,0x1e8,0x1c4};
 unsigned long bo[]={0x3af0,0x288f8,0x28c80,0x28ce4,0x28c1c,0x28a6c,0x3a48};
 for(unsigned i=0;i<7;i++) *(u32*)(profile_text+st[i])=0x14000000U|((bo[i]-st[i])/4);
 profile_funcs.version_info="chip_name = g12b [stb:2.6:e]-[v1.0]-";
 profile_funcs.multi_control_path=(void*)(b+0x1d0);profile_funcs.multi_mp_init=(void*)(b+0x1d4);
 profile_funcs.multi_mp_reset=(void*)(b+0x1e4);profile_funcs.multi_mp_process=(void*)(b+0x1d8);
 profile_funcs.multi_mp_release=(void*)(b+0x1e0);
 memset(&fake_owner,0,sizeof(fake_owner));strcpy(fake_owner.name,"dovi5");
 fake_owner.core_layout.base=profile_text;fake_owner.core_layout.text_size=sizeof(profile_text);fake_owner.live=true;
 blob_funcs=NULL;blob_owner=NULL;blob_text=0;mp_ctx=NULL;
}
static void test_transitions(void) {
 static char comp[32784],md[4096];char src[8]={0};int cl=0,ml=0;
 struct dovi_setting_s out={0};
 setup_mock();out.video_width=1920U<<16;out.video_height=1080U<<16;out.g_bitdepth=8;
 unsigned init_before=init_calls,release_before=release_calls;
 cp_ret=0;process_ret=0;
 for(unsigned i=0;i<50;i++) {
  assert(mp_init_adapter(0)==&context_token);
  assert(mp_reset_adapter(1)==0);
  assert(mp_process_adapter(src,8,comp,&cl,md,&ml,true)==0);
  assert(call_cp(&out)==0);
  mp_release_adapter();
  assert(mp_ctx==&context_token && !mp_attached && mp_ready && fake_owner.refs==0);
  assert(call_cp(&out)==-ENODEV);
  assert(init_calls==init_before && release_calls==release_before);
 }
 mp_release_owned();assert(release_calls==release_before+1 && !mp_ctx);
}
static void test_registration(void) {
 struct module *o;unsigned long b;
 setup_profile();assert(validate_blob(NULL,&o,&b)==-EINVAL);
 assert(validate_blob(&profile_funcs,&o,&b)==0);assert(o==&fake_owner && b==(unsigned long)profile_text);
 profile_funcs.multi_mp_init=(void*)(b+0x1d8);assert(validate_blob(&profile_funcs,&o,&b)==-EINVAL);
 setup_profile();*(u32*)(profile_text+0x1e8)=0;assert(validate_blob(&profile_funcs,&o,&b)==-EINVAL);
 setup_profile();owner_lookup_enabled=false;assert(validate_blob(&profile_funcs,&o,&b)==-EINVAL);owner_lookup_enabled=true;
 setup_profile();profile_funcs.version_info="wrong";assert(register_dv5shim_func(&profile_funcs)==-EINVAL);assert(!blob_funcs);
 /* Register real host callbacks after separately validating profile offsets. */
 setup_mock();blob_funcs=NULL;blob_owner=NULL;mp_ctx=NULL;mp_ready=false;mp_attached=false;
 mock_profile_registration=true;init_calls=release_calls=0;
 init_fail=true;assert(register_dv5shim_func(&mock_funcs)==-ENOMEM);
 assert(!blob_funcs && !blob_owner && !blob_text && !mp_ctx && !mp_ready);
 init_fail=false;reset_ret=-1;assert(register_dv5shim_func(&mock_funcs)==-1);
 assert(!blob_funcs && !blob_owner && !blob_text && !mp_ctx && !mp_ready && release_calls==1);
 reset_ret=0;driver_reg_ret=-EBUSY;assert(register_dv5shim_func(&mock_funcs)==-EBUSY);
 assert(!blob_funcs && !blob_owner && !blob_text && !mp_ctx && release_calls==2);
 driver_reg_ret=0;assert(register_dv5shim_func(&mock_funcs)==0);assert(registered==&adapted_funcs);
 assert(mp_ctx && mp_ready && !mp_attached);assert(init_calls==4);
 assert(register_dv5shim_func(&mock_funcs)==-EBUSY);assert(init_calls==4);
 assert(adapted_funcs.metadata_parser_init(0)==&context_token);
 adapted_funcs.metadata_parser_release();assert(mp_ctx && !mp_attached && release_calls==2);
 assert(adapted_funcs.metadata_parser_init(0)==&context_token);
 driver_unreg_ret=0;assert(unregister_dv5shim_func()==0);
 assert(!blob_funcs && !blob_owner && !blob_text && !registered && !mp_ctx && release_calls==3);
 mock_profile_registration=false;
}

static void graphics_output(struct dovi_setting_s *s) {
 memset(s,0,sizeof(*s));s->video_width=1920U<<16;s->video_height=1080U<<16;
 s->g_format=G_SDR_RGB;s->g_bitdepth=8;
}
static void setup_graphics(struct dovi_setting_s *s) {
 setup_mock();mp_attached=true;graphics_output(s);
 blob_graphics_reference=true;host_graphics_capable=true;
 host_graphics_desired=host_graphics_tracked=true;
 cp_ret=DV5_CP_FLAG_BLOB_CHANGE_TC;cp_bad=0;
 cp_ack_magic=DV_GFX_ACK_MAGIC;
 cp_ack_flags=DV_GFX_ACK_MODE|DV_GFX_ACK_FRESH|DV_GFX_ACK_GRAPHICS;
}
static int call_cp_priority(struct dovi_setting_s *s,enum priority_mode_enum priority) {
 char comp[4]={0},md[8]={0};struct hdr10_parameter hdr={0};
 return cp_adapter(FORMAT_DOVI,FORMAT_DOVI,comp,4,md,8,priority,10,0,0,0,100,0,1000,1,&hdr,s);
}
static void test_graphics_protocol(void) {
 struct dovi_setting_s s,before;
 /* Match the already-tested fixed byte transport, not a guessed struct tail. */
 assert(offsetof(struct m_dovi_setting_s,reserved)+4*DV_GFX_REQUEST_SLOT==19800);
 assert(offsetof(struct m_dovi_setting_s,reserved)+4*DV_GFX_ENABLE_SLOT==19804);
 assert(offsetof(struct m_dovi_setting_s,reserved)+4*DV_GFX_ACK_SLOT==19808);
 assert(offsetof(struct m_dovi_setting_s,reserved)+4*DV_GFX_FLAGS_SLOT==19812);
 assert(offsetof(struct m_dovi_setting_s,reserved)+4*DV_GFX_RETRY_SLOT==19816);
 /* Fresh graphics receipt adds TC2 while preserving original TC flags. */
 setup_graphics(&s);assert(call_cp(&s)==(0x10|0x20));
 assert(host_request_calls==1 && host_request_eligible);
 assert(cp_request[0]==DV_GFX_REQUEST_MAGIC && cp_request[1]==1 && cp_request[4]==1);
 assert(cp_request[2]==0 && cp_request[3]==0); /* ACK never reused as input. */
 assert(host_result_calls==1 && host_result_mode && host_result_fresh);
 assert(host_result_flags==0x10 && host_result.epoch==0x12345678 && host_result.generation==17);
 assert(host_result.enable && host_result.tracked && host_result.retry);
 /* The driver gets a copy of the captured request, not a later preference. */
 assert(host_request.epoch==host_result.epoch && host_request.generation==host_result.generation &&
        host_request.enable==host_result.enable && host_request.retry==host_result.retry &&
        host_request.tracked==host_result.tracked);
 setup_graphics(&s);cp_flip_preference=true;assert(call_cp(&s)==(0x10|0x20));
 assert(!host_graphics_desired && host_result.enable && host_result_mode);
 /* No ACK, wrong magic, unknown bits, and absent graphics fail closed. */
 for(unsigned i=0;i<4;i++) {
  setup_graphics(&s);
  if(i==0)cp_ack_magic=0;
  if(i==1)cp_ack_magic=DV_GFX_ACK_MAGIC^1;
  if(i==2)cp_ack_flags|=8;
  if(i==3)cp_ack_flags&=~DV_GFX_ACK_GRAPHICS;
  assert(call_cp(&s)==0x10 && host_result_calls==0);
 }
 /* A mode mismatch is reported for actual-driver rejection, never forced. */
 setup_graphics(&s);cp_ack_flags&=~DV_GFX_ACK_MODE;
 assert(call_cp(&s)==0x10 && host_result_calls==1);
 assert(host_result.enable && !host_result_mode);
 /* Current CONST remains authoritative even after fresh concatenation. */
 setup_graphics(&s);cp_ret|=DV5_CP_FLAG_CONST_TC2;
 assert(call_cp(&s)==(0x10|DV5_CP_FLAG_CONST_TC2));
 assert(host_result_calls==1 && host_result_fresh);
 assert(host_result_flags==(0x10|DV5_CP_FLAG_CONST_TC2));
 /* Cached ACK and untracked native history do not synthesize TC2. */
 setup_graphics(&s);cp_ack_flags&=~DV_GFX_ACK_FRESH;
 assert(call_cp(&s)==0x10 && host_result_calls==1 && !host_result_fresh);
 setup_graphics(&s);host_graphics_desired=host_graphics_tracked=false;
 cp_ack_flags=DV_GFX_ACK_FRESH|DV_GFX_ACK_GRAPHICS;
 assert(call_cp(&s)==0x10 && !host_result.tracked && !cp_request[1] && !cp_request[4]);
 /* Route exits request native graphics; a tracked fresh native ACK permits
  * recovery. Actual driver applied-state/DMA handling is tested separately. */
 for(unsigned i=0;i<5;i++) {
  setup_graphics(&s);
  if(i==0)s.g_format=G_HDR_RGB;
  if(i==1)s.g_bitdepth=10;
  if(i==2)s.use_ll_flag=1;
  if(i==3)s.ll_rgb_desired=1;
  if(i==4)s.dovi2hdr10_nomapping=1;
  cp_ack_flags=DV_GFX_ACK_FRESH|DV_GFX_ACK_GRAPHICS;
  assert(call_cp(&s)==(0x10|0x20));
  assert(!host_request_eligible && !cp_request[1] && cp_request[4]);
  assert(host_result_calls==1 && !host_result_mode && !host_result.enable);
 }
 setup_graphics(&s);cp_ack_flags=DV_GFX_ACK_FRESH|DV_GFX_ACK_GRAPHICS;
 assert(call_cp_priority(&s,G_PRIORITY)==(0x10|0x20));
 assert(!host_request_eligible && !cp_request[1] && !host_result.enable);
 /* CP failure or bounds rejection cannot publish an otherwise valid ACK. */
 setup_graphics(&s);before=s;cp_ret=-2;
 assert(call_cp(&s)==-2 && host_result_calls==0 && !memcmp(&s,&before,sizeof(s)));
 for(unsigned i=1;i<=5;i++) {
  setup_graphics(&s);before=s;cp_bad=i;
  assert(call_cp(&s)==-EOVERFLOW && host_result_calls==0 && !memcmp(&s,&before,sizeof(s)));
 }
 cp_bad=0;
 /* Unsupported registration cannot opt in even if a mock CP invents an ACK. */
 setup_graphics(&s);blob_graphics_reference=false;host_graphics_capable=false;
 assert(call_cp(&s)==0x10 && host_request_calls==0 && host_result_calls==0);
 for(unsigned i=0;i<5;i++)assert(cp_request[i]==0);
}
static void test_graphics_registration(void) {
 const char *versions[]={
  "chip_name = g12b [stb:2.6:e]-[v1.0]-",
  "chip_name = g12b [stb:2.6:e]-[v1.0]-[p3i-gfxref:1]",
  "chip_name = g12b [stb:2.6:e]-[v1.0]-[p3i-gfxref:3]",
  "chip_name = g12b [stb:2.6:e]-[v1.0]-" DV_GFX_CAPABILITY};
 for(unsigned i=0;i<4;i++) {
  setup_mock();blob_funcs=NULL;blob_owner=NULL;mp_ctx=NULL;mp_ready=false;mp_attached=false;
  mock_profile_registration=true;init_fail=false;reset_ret=driver_reg_ret=driver_unreg_ret=0;
  mock_funcs.version_info=versions[i];
  assert(register_dv5shim_func(&mock_funcs)==0);
  assert(blob_graphics_reference==(i==3) && host_graphics_capable==(i==3));
  assert(host_capability_calls==1);
  assert(unregister_dv5shim_func()==0);
  assert(!blob_graphics_reference && !host_graphics_capable && host_capability_calls==2);
 }
 setup_mock();blob_funcs=NULL;blob_owner=NULL;mp_ctx=NULL;mp_ready=false;mp_attached=false;
 mock_funcs.version_info=versions[3];mock_profile_registration=true;driver_reg_ret=-EBUSY;
 assert(register_dv5shim_func(&mock_funcs)==-EBUSY);
 assert(!blob_graphics_reference && !host_graphics_capable && !blob_funcs && !mp_ctx);
 assert(host_capability_calls==2); /* Capability withdrawn on registration rollback. */
 driver_reg_ret=0;mock_profile_registration=false;
}
static unsigned cfi_real_calls;
static void fake_checker(u64 id,void *target,void *diag) {
 assert(id==0xf7247573865c0423ULL);assert((unsigned long)target==(unsigned long)fake_checker+0x1e8);
 (void)diag;cfi_real_calls++;
}
static void fatal_stack(void) { __stack_chk_fail(); }
static void fatal_ubsan(void) { __ubsan_handle_cfi_check_fail_abort(NULL,NULL,NULL); }
static void fatal_cfi(void) { __cfi_slowpath_diag(0x69cb7240b75618e2ULL,NULL,NULL); }
static void expect_abort(void (*f)(void)) {
 pid_t pid=fork();assert(pid>=0);if(!pid){f();_exit(1);}int st=0;assert(waitpid(pid,&st,0)==pid);
 assert(WIFSIGNALED(st) && WTERMSIG(st)==SIGABRT);
}
static void test_cfi(void) {
 unsigned long b=0x100000;
 assert(dv5_cfi_allowed(0xf7247573865c0423ULL,b+0x1e8,b));
 assert(dv5_cfi_allowed(0xf5d57d915c360469ULL,b+0x1c4,b));
 assert(!dv5_cfi_allowed(0xf7247573865c0423ULL,b+0x1c4,b));
 assert(!dv5_cfi_allowed(0xf5d57d915c360469ULL,b+0x1e8,b));
 assert(!dv5_cfi_allowed(0xf7247573865c0423ULL,0x1e8,0));
 assert(!dv5_cfi_allowed(0x69cb7240b75618e2ULL,b+0x1e8,b));
 assert(!dv5_cfi_allowed(0x789936d4dd313e00ULL,b+0x1e8,b));
 blob_text=(unsigned long)fake_checker;
 __cfi_slowpath_diag(0xf7247573865c0423ULL,(void*)(blob_text+0x1e8),NULL);
 assert(cfi_real_calls==1 && dvshim_cfi_hits==1);
 expect_abort(fatal_cfi);expect_abort(fatal_stack);expect_abort(fatal_ubsan);
}
int main(void) {
 assert(dv_compat_shim_init()==0);assert(dv5_stack_chk_guard && !(dv5_stack_chk_guard&0xff));
 test_cp();test_mp();test_transitions();test_registration();test_graphics_protocol();test_graphics_registration();test_cfi();
 puts("PASS: CP error/output/range contracts, sleepable parser lifetime/attach/reset/length guards, transactional registration, strict CFI and fatal failure hooks; actual shim graphics capability/request/ACK/CONST/error contracts (driver services mocked)");
 return 0;
}
'''
tests=tests.replace(' m->dm_reg1; /* removed below; keep mocking the real newer nested field */\n','')
# dm_reg_ipcore3 uses d2c coefficient fields copied from actual source.
combined=preamble+public+private+abi+policy+services+source+tests
flags=['-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable','-Wno-unused-parameter','-fsanitize=address,undefined','-fno-omit-frame-pointer']
with tempfile.TemporaryDirectory(prefix='dv-shim-test-') as td:
    c=Path(td)/'shim.c';binary=Path(td)/'shim';c.write_text(combined)
    subprocess.run(['gcc',*flags,str(c),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})

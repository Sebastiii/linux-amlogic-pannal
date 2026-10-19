#!/usr/bin/env python3
"""Compile and execute the actual bounded kernel diagnostic core with host APIs.

No device, full-kernel build or rewritten diagnostic algorithm. Linux primitives
below are explicit stand-ins; use real CE syntax checks for compiler acceptance.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--root', type=Path, default=ROOT)
options, unittest_args = parser.parse_known_args()
ROOT = options.root.resolve()
CORE = ROOT / 'drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/audio_diagnostics.c'
HEADER = ROOT / 'include/linux/amlogic/media/sound/audio_diagnostics.h'
MOCK = r'''
#ifndef AAD_MOCK_H
#define AAD_MOCK_H
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <limits.h>
#include <sys/types.h>
typedef unsigned long long u64;
typedef unsigned int u32;
#define IS_REACHABLE(x) (x)
#define NSEC_PER_SEC 1000000000ULL
#ifndef PAGE_SIZE
#define PAGE_SIZE 4096
#endif
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define EXPORT_SYMBOL_GPL(x)
#define GFP_KERNEL 0
static u64 mock_now = 1000000000ULL;
static unsigned int mock_clocks, mock_allocs, mock_frees;
static bool mock_busy, mock_nomem;
static void (*mock_on_try)(void);
static u64 ktime_get_ns(void) { mock_clocks++; return mock_now; }
typedef struct { int value; } atomic_t;
#define ATOMIC_INIT(x) {x}
static void atomic_inc(atomic_t *p) { p->value++; }
static void atomic_set(atomic_t *p, int v) { p->value=v; }
static int atomic_read(const atomic_t *p) { return p->value; }
#define DEFINE_SPINLOCK(x) int x=0
static bool mock_trylock(int *p) { if(mock_on_try) { void(*hook)(void)=mock_on_try; mock_on_try=NULL; hook(); } if (mock_busy || *p) return false; *p=1; return true; }
#define spin_trylock_irqsave(p,f) ((f)=0,mock_trylock(p))
#define spin_lock_irqsave(p,f) do { (f)=0; if (!mock_trylock(p)) abort(); } while(0)
#define spin_unlock_irqrestore(p,f) do { (void)(f); *(p)=0; } while(0)
static void *kmalloc(size_t n,int flags) { (void)flags; mock_allocs++; return mock_nomem?NULL:malloc(n); }
static void kfree(void *p) { mock_frees++; free(p); }
static int scnprintf(char *b,size_t n,const char *fmt,...) {
 va_list ap; int got; if (!n) return 0; va_start(ap,fmt); got=vsnprintf(b,n,fmt,ap); va_end(ap);
 return got<0?0:got>=(int)n?(int)n-1:got;
}
/* Range-checked kernel string APIs, only if the production parser uses them. */
static int kstrtouint(const char *s,unsigned int base,unsigned int *out) {
 char *end; unsigned long long v; if (*s=='-') return -EINVAL; errno=0; v=strtoull(s,&end,base);
 if (errno==ERANGE || v>UINT_MAX) return -ERANGE;
 if (end==s || (*end && strcmp(end,"\n"))) return -EINVAL; *out=(unsigned int)v; return 0;
}
static int kstrtoull(const char *s,unsigned int base,unsigned long long *out) {
 char *end; unsigned long long v; if (*s=='-') return -EINVAL; errno=0; v=strtoull(s,&end,base);
 if (errno==ERANGE) return -ERANGE;
 if (end==s || (*end && strcmp(end,"\n"))) return -EINVAL; *out=v; return 0;
}
#endif
'''
HARNESS = r'''
#include "kernel_mock.h"
#include "ACTUAL_CORE"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line%d: %s\n",__LINE__,#x); return 1; } } while(0)
static char snapshot[PAGE_SIZE+64];
static const char *show(void) { memset(snapshot,'Z',sizeof(snapshot)); ssize_t n=aml_audio_diag_show(snapshot); if(n<0) return "UNAVAILABLE"; if(n>=PAGE_SIZE) abort(); snapshot[n]=0; return snapshot; }
static struct aad_slot *slot(unsigned int k,unsigned int id) { unsigned int i; for(i=0;i<AAD_SLOTS;i++) if(aad.slots[i].kind==k&&aad.slots[i].id==id) return &aad.slots[i]; return NULL; }
static int off(void) {
 unsigned int i; for(i=0;i<100;i++) { CHECK(!aml_audio_diag_enabled()); aml_audio_diag_record(AAD_POINTER,2,0,0,0); }
 CHECK(mock_clocks==0); CHECK(mock_allocs==0); return 0;
}
static int parse(void) {
 const char *invalid[]={"", "start 0 1", "start 901 1", "start 1 0", "start 1 2 garbage", "start 4294967297 1", "start 1 -1", "start 1 18446744073709551616", "stop 0", "stop -1", "stop 1 junk"};
 unsigned int i; for(i=0;i<ARRAY_SIZE(invalid);i++) { int r=aml_audio_diag_control(invalid[i]); CHECK(r<0); CHECK(!aml_audio_diag_enabled()); }
 CHECK(aml_audio_diag_control("start 900 123\n")==0); CHECK(aml_audio_diag_control("start 1 456")==-EBUSY); CHECK(aml_audio_diag_control("stop 456")==-EPERM); CHECK(aml_audio_diag_enabled()); CHECK(aml_audio_diag_control("stop 123\n")==0); CHECK(!aml_audio_diag_enabled()); return 0;
}
static int deadline(void) {
 CHECK(aml_audio_diag_control("start 1 123")==0); mock_now+=NSEC_PER_SEC-1; CHECK(aml_audio_diag_enabled()); aml_audio_diag_record(AAD_PRIMARY,12,0,0,0); CHECK(slot(AAD_PRIMARY,12)->count==1);
 mock_now++; CHECK(!aml_audio_diag_enabled()); aml_audio_diag_record(AAD_PRIMARY,12,0,0,0); CHECK(slot(AAD_PRIMARY,12)->count==1); CHECK(strstr(show(),"active=0")); CHECK(aml_audio_diag_control("start 1 124")==0); CHECK(!slot(AAD_PRIMARY,12)); return 0;
}
static int contention(void) {
 CHECK(aml_audio_diag_control("start 900 1")==0); unsigned int before=mock_allocs; mock_busy=true; aml_audio_diag_record(AAD_POINTER,2,20,0,0); mock_busy=false;
 CHECK(atomic_read(&aad_lost)==1); CHECK(!slot(AAD_POINTER,2)); CHECK(mock_allocs==before); mock_busy=true; CHECK(strstr(show(),"UNAVAILABLE busy")); mock_busy=false; CHECK(mock_allocs==mock_frees);
 mock_nomem=true; CHECK(aml_audio_diag_show(snapshot)==-ENOMEM); mock_nomem=false; return 0;
}
static int capacity(void) {
 unsigned int i; CHECK(aml_audio_diag_control("start 900 1")==0);
 for(i=0;i<AAD_SLOTS+3;i++) aml_audio_diag_record(AAD_PRIMARY,100+i,0,0,0);
 CHECK(atomic_read(&aad_lost)==3); CHECK(!slot(AAD_PRIMARY,100+AAD_SLOTS));
 for(i=0;i<AAD_EVENTS+5;i++) { mock_now++; aml_audio_diag_record(AAD_MODE,20,i,0,0); }
 CHECK(AAD_EVENTS==32); CHECK(aad.seq==AAD_EVENTS+5); CHECK(aad.overwritten==5); CHECK(strstr(show(),"overwritten=5")); CHECK(!strstr(snapshot,"\nE ")); CHECK(strstr(snapshot,"\nS "));
 ssize_t n=aml_audio_diag_events_show(snapshot); CHECK(n>0&&n<PAGE_SIZE); CHECK(!strstr(snapshot,"\nR ")&&!strstr(snapshot,"\nS ")); CHECK(strstr(snapshot,"overwritten=5")); CHECK(strstr(snapshot,"\nE 6 ")); CHECK(strstr(snapshot,"END truncated=0"));
 unsigned int events=0; char *at=snapshot;while((at=strstr(at,"\nE "))) {events++;at+=3;} CHECK(events==AAD_EVENTS);
 CHECK(aad.seq==AAD_EVENTS+5&&aad.overwritten==5); CHECK(mock_allocs==mock_frees); return 0;
}
static int irq(void) {
 CHECK(aml_audio_diag_control("start 900 1")==0); aml_audio_diag_record(AAD_PRIMARY,12,0,0,0); mock_now+=100;
 aml_audio_diag_record(AAD_PRIMARY,12,0,0,0); mock_now+=200; aml_audio_diag_record(AAD_THREAD,12,2,3,0);
 CHECK(slot(AAD_THREAD,12)->dispatch_max==300); CHECK(slot(AAD_PRIMARY,12)->pending==0);
 mock_now+=700; aml_audio_diag_record(AAD_THREAD_EXIT,12,2,0,0); CHECK(slot(AAD_THREAD_EXIT,12)->dispatch_max==700); CHECK(slot(AAD_THREAD,12)->pending==0);
 CHECK(slot(AAD_PRIMARY,12)->count==2); CHECK(slot(AAD_THREAD,12)->count==1); CHECK(mock_allocs==0); return 0;
}
static int lifecycle(void) {
 CHECK(aml_audio_diag_control("start 900 1")==0); aml_audio_diag_record(AAD_PREPARE,2,44100,2205,8820);
 CHECK(aad.routes[2].epoch==1&&aad.routes[2].rate==44100); aml_audio_diag_record(AAD_POINTER,2,8000,8820,3); mock_now++; aml_audio_diag_record(AAD_POINTER,2,8000,8820,3); CHECK(slot(AAD_POINTER,2)->repeats==1);
 mock_now++; aml_audio_diag_record(AAD_POINTER,2,100,8820,3); CHECK(slot(AAD_POINTER,2)->wraps==1);
 aml_audio_diag_record(AAD_TRIGGER,2,1,3,2); CHECK(aad.routes[2].state!=0); aml_audio_diag_record(AAD_CLOSE,2,0,0,0); CHECK(aad.routes[2].state==0); aml_audio_diag_record(AAD_FREE,2,0,0,0); CHECK(aad.routes[2].state==0);
 aml_audio_diag_record(AAD_PREPARE,2,48000,2400,9600); CHECK(aad.routes[2].epoch==2&&aad.routes[2].rate==48000); CHECK(aad.routes[2].period==2400&&aad.routes[2].buffer==9600); CHECK(slot(AAD_POINTER,2)->count==0); CHECK(slot(AAD_POINTER,2)->last==0); aml_audio_diag_record(AAD_POINTER,2,0,9600,3); CHECK(slot(AAD_POINTER,2)->wraps==0&&slot(AAD_POINTER,2)->repeats==0); return 0;
}
static int truncation(void) {
 unsigned int i; CHECK(aml_audio_diag_control("start 900 18446744073709551615")==0);
 for(i=0;i<AAD_SLOTS;i++) { struct aad_slot *s=&aad.slots[i]; memset(s,255,sizeof(*s)); }
 aad.seq=~0ULL-1; aad.overwritten=~0ULL;
 for(i=0;i<AAD_EVENTS;i++) memset(&aad.events[i],255,sizeof(aad.events[i]));
 for(unsigned int events_only=0;events_only<2;events_only++) {
 memset(snapshot,'Q',sizeof(snapshot)); ssize_t n=events_only?aml_audio_diag_events_show(snapshot):aml_audio_diag_show(snapshot); CHECK(n>0&&n<PAGE_SIZE); CHECK(strstr(snapshot,PAGE_SIZE<4096?"END truncated=1":"END truncated=0")); for(i=PAGE_SIZE;i<sizeof(snapshot);i++) CHECK(snapshot[i]=='Q');
 if(events_only) CHECK(!strstr(snapshot,"\nS ")&&!strstr(snapshot,"\nR ")); else CHECK(!strstr(snapshot,"\nE "));
 }
 CHECK(mock_allocs==mock_frees); return 0;
}
static void replace_owner(void) { mock_now+=1000; if(aml_audio_diag_control("stop 1")||aml_audio_diag_control("start 900 2"))abort(); }
static int race(void) { CHECK(!aml_audio_diag_control("start 900 1")); mock_on_try=replace_owner; aml_audio_diag_record(AAD_POINTER,2,4,5,6); CHECK(aad.owner==2); CHECK(!slot(AAD_POINTER,2)); CHECK(atomic_read(&aad_lost)==1); return 0; }
static void replace_owner_equal_clock(void) { if(aml_audio_diag_control("stop 1")||aml_audio_diag_control("start 900 2"))abort(); }
static void replace_owner_reused_token(void) { if(aml_audio_diag_control("stop 1")||aml_audio_diag_control("start 900 1"))abort(); }
static int race_equal_clock(bool reused_token) {
 CHECK(!aml_audio_diag_control("start 900 1"));
 mock_on_try=reused_token?replace_owner_reused_token:replace_owner_equal_clock;
 aml_audio_diag_record(AAD_POINTER,2,4,5,6);
 CHECK(aad.owner==(reused_token?1:2)); CHECK(!slot(AAD_POINTER,2));
 CHECK(atomic_read(&aad_lost)==1);
 /* A current-session observation at the same tick must still be accepted. */
 aml_audio_diag_record(AAD_POINTER,2,7,8,9);
 CHECK(slot(AAD_POINTER,2)&&slot(AAD_POINTER,2)->count==1);
 CHECK(slot(AAD_POINTER,2)->a==7); return 0;
}
int main(int argc,char **argv) {
 if(argc!=2) return 2; if(!strcmp(argv[1],"race"))return race();
 if(!strcmp(argv[1],"equal-clock"))return race_equal_clock(false);
 if(!strcmp(argv[1],"reused-token"))return race_equal_clock(true);
 if(!strcmp(argv[1],"off"))return off(); if(!strcmp(argv[1],"parse"))return parse(); if(!strcmp(argv[1],"deadline"))return deadline(); if(!strcmp(argv[1],"contention"))return contention(); if(!strcmp(argv[1],"capacity"))return capacity(); if(!strcmp(argv[1],"irq"))return irq(); if(!strcmp(argv[1],"lifecycle"))return lifecycle(); if(!strcmp(argv[1],"truncation"))return truncation(); return 2;
}
'''

class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='aad-core-test-')
        root=Path(cls.temp.name); cls.root=root
        (root/'linux').mkdir()
        (root/'kernel_mock.h').write_text(MOCK)
        for h in ('kernel.h','slab.h','spinlock.h','timekeeping.h','atomic.h','export.h','string.h','kconfig.h','types.h'):
            (root/'linux'/h).write_text('#include "kernel_mock.h"\n')
        (root/'harness.c').write_text(HARNESS.replace('ACTUAL_CORE',str(CORE)))
        cls.binary=root/'core-test'
        command=['gcc','-std=gnu99','-O2','-Wall','-Wextra','-Wno-unused-function','-Wno-unused-parameter','-Wno-misleading-indentation','-DCONFIG_AMLOGIC_HDMITX=1','-I'+str(root),'-I'+str(ROOT/'include'),str(root/'harness.c'),'-o',str(cls.binary)]
        compiled=subprocess.run(command,capture_output=True,text=True)
        if compiled.returncode: raise AssertionError(compiled.stdout+compiled.stderr)
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def run_case(self,name):
        result=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_old_timestamp_cannot_enter_new_owner_epoch(self): self.run_case('race')
    def test_equal_clock_cannot_admit_previous_owner_event(self): self.run_case('equal-clock')
    def test_reused_owner_token_still_changes_epoch_at_same_clock(self): self.run_case('reused-token')
    def test_disabled_has_no_clock_or_allocation(self): self.run_case('off')
    def test_strict_parser_and_ownership(self): self.run_case('parse')
    def test_deadline_and_new_owner(self): self.run_case('deadline')
    def test_contention_and_allocation_errors(self): self.run_case('contention')
    def test_fixed_slots_and_ring_overwrite(self): self.run_case('capacity')
    def test_irq_coalescing_dispatch_and_duration(self): self.run_case('irq')
    def test_pointer_offsets_and_lifecycle_epochs(self): self.run_case('lifecycle')
    def test_snapshot_truncation_is_explicit_and_bounded(self): self.run_case('truncation')
    def test_actual_hardware_observer_disabled_gates_and_bounded_reads(self):
        source=(ROOT/'drivers/amlogic/media/vout/hdmitx/hdmi_tx_20/hw/hdmi_tx_hw.c').read_text()
        start=source.index('ssize_t aml_audio_diag_hw_show(char *buf)')
        first=source.index('{',start); depth=1; end=first+1
        while depth:
            depth += (source[end]=='{')-(source[end]=='}');end+=1
        body=source[start:end]
        constants=sorted(set(re.findall(r'\b(?:HDMITX_DWC_[A-Z0-9_]+|MESON_CPU_ID_G12[AB]|P_HHI_HDMI_PLL_CNTL)\b',body)))
        definitions='\n'.join(f'#define {name} {i+1}' for i,name in enumerate(constants))
        standins=r"""
struct hdmitx_dev { unsigned int chip_type,ready; };
static struct hdmitx_dev device;
static bool missing_device,unstable_cts;
static unsigned int reads,cts_reads;
static struct hdmitx_dev *get_hdmitx_device(void) { return missing_device?NULL:&device; }
static unsigned int hdmitx_rd_reg(unsigned int reg) { reads++; if(reg==HDMITX_DWC_AUD_CTS1)return unstable_cts?cts_reads++:0xCC; if(reg==HDMITX_DWC_AUD_N1)return 0x63; if(reg==HDMITX_DWC_AUD_N2)return 0x12; return 0; }
static unsigned int hd_read_reg(unsigned int reg) { (void)reg; reads++; return 0; }
#define HHI_REG_ADDR(x) (x)
"""
        checks=r"""
int main(void) {
 char b[PAGE_SIZE];device.chip_type=MESON_CPU_ID_G12B;device.ready=1;
 if(aml_audio_diag_hw_show(b)<=0||reads||!strstr(b,"UNAVAILABLE"))return 1;
 if(aml_audio_diag_control("start 900 1"))return 2;
 missing_device=true;aml_audio_diag_hw_show(b);if(reads)return 3;missing_device=false;
 device.ready=0;aml_audio_diag_hw_show(b);if(reads)return 4;device.ready=1;
 device.chip_type=999;aml_audio_diag_hw_show(b);if(reads||!strstr(b,"unsupported"))return 5;
 device.chip_type=MESON_CPU_ID_G12B;aml_audio_diag_hw_show(b);if(reads>32||!strstr(b,"stable=1")||!strstr(b,"n=4707"))return 6;
 reads=0;unstable_cts=true;aml_audio_diag_hw_show(b);if(reads>32||!strstr(b,"stable=0"))return 7;
 reads=0;mock_now+=901*NSEC_PER_SEC;aml_audio_diag_hw_show(b);if(reads||!strstr(b,"UNAVAILABLE"))return 8;return 0;
}
"""
        path=self.root/'hardware.c';path.write_text('#include "kernel_mock.h"\n#include "'+str(CORE)+'"\n'+definitions+'\n'+standins+'\n'+body+'\n'+checks)
        binary=self.root/'hardware';result=subprocess.run(['gcc','-std=gnu99','-DCONFIG_AMLOGIC_HDMITX=1','-I'+str(self.root),'-I'+str(ROOT/'include'),str(path),'-o',str(binary)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(subprocess.run([str(binary)]).returncode,0)

    def test_both_snapshot_truncation_footers_with_reduced_page_service(self):
        # A 1KiB PAGE_SIZE service forces truncation; the production 4KiB ABI
        # fits its independent worst-case rows. This executes the same core.
        binary=self.root/'small-page'
        result=subprocess.run(['gcc','-std=gnu99','-O2','-DPAGE_SIZE=1024','-DCONFIG_AMLOGIC_HDMITX=1','-I'+str(self.root),'-I'+str(ROOT/'include'),str(self.root/'harness.c'),'-o',str(binary)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        result=subprocess.run([str(binary),'truncation'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_hdmi_disabled_header_stubs(self):
        path=self.root/'disabled.c';path.write_text('#include <linux/amlogic/media/sound/audio_diagnostics.h>\nint main(void){if(aml_audio_diag_enabled())return 1;aml_audio_diag_record(AAD_POINTER,2,3,4,5);return mock_clocks||mock_allocs;}\n')
        binary=self.root/'disabled';result=subprocess.run(['gcc','-std=gnu99','-I'+str(self.root),'-I'+str(ROOT/'include'),str(path),'-o',str(binary)],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(subprocess.run([str(binary)]).returncode,0)

if __name__=='__main__': unittest.main(argv=[__file__]+unittest_args)

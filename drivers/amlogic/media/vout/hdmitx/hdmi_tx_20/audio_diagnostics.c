// SPDX-License-Identifier: GPL-2.0
/* Opt-in bounded scalar telemetry. No worker, printk, hardware writes or
 * allocations in producer paths. Readers never retain live driver pointers.
 */
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/timekeeping.h>
#include <linux/atomic.h>
#include <linux/export.h>
#include <linux/string.h>
#include <linux/amlogic/media/sound/audio_diagnostics.h>

#define AAD_SLOTS 16
#define AAD_EVENTS 32
struct aad_slot {
	u32 kind, id, count, a, b, c, repeats, wraps;
	u64 last, gap_max, gap_at, pending, dispatch_max, dispatch_at;
};
struct aad_event { u64 seq, ns; u32 kind, id, a, b, c; };
struct aad_route { u32 epoch, rate, period, buffer, state; u64 ns; };
struct aad_state {
	u64 owner, start, deadline, seq, overwritten;
	struct aad_slot slots[AAD_SLOTS];
	struct aad_event events[AAD_EVENTS];
	struct aad_route routes[4];
};
static DEFINE_SPINLOCK(aad_lock);
static struct aad_state aad;
static atomic_t aad_lost = ATOMIC_INIT(0);
static bool aad_on;
static u64 aad_generation;

bool aml_audio_diag_enabled(void)
{
	return READ_ONCE(aad_on) && ktime_get_ns() < READ_ONCE(aad.deadline);
}
EXPORT_SYMBOL_GPL(aml_audio_diag_enabled);

void aml_audio_diag_record(unsigned int kind, unsigned int id, u32 a, u32 b, u32 c)
{
	unsigned long flags;
	u64 now, gap, generation;
	unsigned int i;
	struct aad_slot *s = NULL, *primary = NULL;
	struct aad_event *event;

	if (!READ_ONCE(aad_on))
		return;
	generation = READ_ONCE(aad_generation);
	now = ktime_get_ns();
	if (!spin_trylock_irqsave(&aad_lock, flags)) {
		atomic_inc(&aad_lost);
		return;
	}
	if (!aad_on || now >= aad.deadline) {
		aad_on = false;
		spin_unlock_irqrestore(&aad_lock, flags);
		return;
	}
	if (generation != aad_generation || now < aad.start) {
		atomic_inc(&aad_lost);
		goto done;
	}
	if (kind <= AAD_FRDDR_STATUS) {
		for (i = 0; i < AAD_SLOTS; i++) {
			if (aad.slots[i].kind == (kind == AAD_THREAD_EXIT ? AAD_THREAD : AAD_PRIMARY) && aad.slots[i].id == id)
				primary = &aad.slots[i];
			if (aad.slots[i].kind == kind && aad.slots[i].id == id)
				s = &aad.slots[i];
		}
		if (!s) {
			for (i = 0; i < AAD_SLOTS; i++)
				if (!aad.slots[i].kind) {
					s = &aad.slots[i];
					s->kind = kind;
					s->id = id;
					break;
				}
		}
		if (!s) {
			atomic_inc(&aad_lost);
			goto done;
		}
		gap = s->last ? now - s->last : 0;
		if (gap > s->gap_max) { s->gap_max = gap; s->gap_at = now; }
		if ((kind == AAD_PRIMARY || kind == AAD_THREAD) && !s->pending)
			s->pending = now;
		if ((kind == AAD_THREAD || kind == AAD_THREAD_EXIT) && primary && primary->pending) {
			gap = now - primary->pending;
			if (gap > s->dispatch_max) {
				s->dispatch_max = gap; s->dispatch_at = now;
			}
			primary->pending = 0;
		}
		if (kind == AAD_POINTER && s->count) {
			if (s->a == a) s->repeats++;
			if (s->a > a) s->wraps++;
		}
		s->count++; s->last = now; s->a = a; s->b = b; s->c = c;
	} else {
		if (id < ARRAY_SIZE(aad.routes)) {
			struct aad_route *r = &aad.routes[id];
			if (kind == AAD_PREPARE) {
				r->epoch++; r->rate = a; r->period = b; r->buffer = c;
				r->state = 1; r->ns = now;
				for (i = 0; i < AAD_SLOTS; i++) {
					struct aad_slot *slot = &aad.slots[i];
					if ((slot->kind == AAD_POINTER && slot->id == id) ||
					    ((slot->kind == AAD_THREAD || slot->kind == AAD_THREAD_EXIT) && slot->a == id)) {
						slot->last = 0; slot->pending = 0;
						slot->count = 0; slot->repeats = 0; slot->wraps = 0;
						slot->gap_max = 0; slot->dispatch_max = 0;
					}
				}

			} else if (kind == AAD_TRIGGER || kind == AAD_CLOSE || kind == AAD_FREE) {
				r->state = kind == AAD_TRIGGER ? 0x100 | a : 0;
				r->ns = now;
			}
		}
		if (aad.seq >= AAD_EVENTS) aad.overwritten++;
		event = &aad.events[aad.seq % AAD_EVENTS];
		event->seq = ++aad.seq; event->ns = now;
		event->kind = kind; event->id = id;
		event->a = a; event->b = b; event->c = c;
	}
done:
	spin_unlock_irqrestore(&aad_lock, flags);
}
EXPORT_SYMBOL_GPL(aml_audio_diag_record);

int aml_audio_diag_control(const char *buf)
{
 unsigned long flags;
 unsigned int seconds = 0;
 unsigned long long owner;
 char op[8], one[32], two[32], tail;
 int fields;
 u64 now = ktime_get_ns();
 bool start;

 fields = sscanf(buf, "%7s %31s %31s %c", op, one, two, &tail);
 start = fields == 3 && !strcmp(op, "start");
 if (start) {
  if (strspn(one, "0123456789") != strlen(one) ||
      strspn(two, "0123456789") != strlen(two) ||
      kstrtouint(one, 10, &seconds) || kstrtoull(two, 10, &owner) ||
      !seconds || seconds > 900 || !owner) return -EINVAL;
 } else {
  if (fields != 2 || strcmp(op, "stop") ||
      strspn(one, "0123456789") != strlen(one) ||
      kstrtoull(one, 10, &owner) || !owner) return -EINVAL;
 }
 spin_lock_irqsave(&aad_lock, flags);
 if (start) {
  if (aad_on && now < aad.deadline) {
   spin_unlock_irqrestore(&aad_lock, flags);
   return -EBUSY;
  }
  aad_on = false;
  aad_generation++;
  memset(&aad, 0, sizeof(aad));
  atomic_set(&aad_lost, 0);
  aad.owner = owner; aad.start = now;
  aad.deadline = now + (u64)seconds * NSEC_PER_SEC;
  aad_on = true;
 } else {
  if (aad.owner != owner) {
   spin_unlock_irqrestore(&aad_lock, flags);
   return -EPERM;
  }
  aad_on = false;
 }
 spin_unlock_irqrestore(&aad_lock, flags);
 return 0;
}

static ssize_t aad_show(char *buf, bool events_only)
{
	struct aad_state *copy;
	unsigned long flags;
	u64 now = ktime_get_ns(), first, n;
	unsigned int i;
	int pos = 0, lost;
	bool active;

	copy = kmalloc(sizeof(*copy), GFP_KERNEL);
	if (!copy) return -ENOMEM;
	if (!spin_trylock_irqsave(&aad_lock, flags)) {
		kfree(copy);
		return scnprintf(buf, PAGE_SIZE, "UNAVAILABLE busy\n");
	}
	if (aad_on && now >= aad.deadline) aad_on = false;
	active = aad_on;
	memcpy(copy, &aad, sizeof(*copy));
	lost = atomic_read(&aad_lost);
	spin_unlock_irqrestore(&aad_lock, flags);
	pos += scnprintf(buf + pos, PAGE_SIZE - pos,
		"AAD1 now=%llu active=%u owner=%llu start=%llu deadline=%llu lost=%d seq=%llu overwritten=%llu\n",
		now, active, copy->owner, copy->start, copy->deadline,
		lost, copy->seq, copy->overwritten);
	for (i = 0; !events_only && i < 4; i++) {
		struct aad_route *r = &copy->routes[i];
		pos += scnprintf(buf + pos, PAGE_SIZE - pos, "R %u %u %u %u %u %u %llu\n",
			i, r->epoch, r->rate, r->period, r->buffer, r->state, r->ns);
	}
	for (i = 0; !events_only && i < AAD_SLOTS; i++) {
		struct aad_slot *s = &copy->slots[i];
		if (!s->kind) continue;
		pos += scnprintf(buf + pos, PAGE_SIZE - pos,
			"S %u %u %u %llu %llu %llu %llu %llu %u %u %u %u %u\n",
			s->kind, s->id, s->count, s->last, s->gap_max, s->gap_at,
			s->dispatch_max, s->dispatch_at, s->a, s->b, s->c, s->repeats, s->wraps);
	}
	first = copy->seq > AAD_EVENTS ? copy->seq - AAD_EVENTS : 0;
	for (n = first; events_only && n < copy->seq; n++) {
		struct aad_event *e = &copy->events[n % AAD_EVENTS];
		pos += scnprintf(buf + pos, PAGE_SIZE - pos, "E %llu %llu %u %u %u %u %u\n",
			e->seq, e->ns, e->kind, e->id, e->a, e->b, e->c);
	}
	if (pos >= PAGE_SIZE - 64)
		pos = PAGE_SIZE - 64;
	pos += scnprintf(buf + pos, PAGE_SIZE - pos, "END truncated=%u snapshot_bytes=%d\n", pos >= PAGE_SIZE - 64, pos);
	kfree(copy);
	return pos;
}

ssize_t aml_audio_diag_show(char *buf) { return aad_show(buf, false); }
ssize_t aml_audio_diag_events_show(char *buf) { return aad_show(buf, true); }

---
name: cta-coach-breakdown
description: >-
  Produce Caio Terra Association (CTA) technical film-study breakdowns, corner cues, 3-panel pointer boards, and 0.4x slow-motion clips for Brazilian Jiu-Jitsu matches using RollMetrics. Activate this skill when coaching an athlete from match video, diagnosing positional grip/hip geometry, citing CTA curriculum timestamps, or generating visual study assets.
---

# Caio Terra Association (CTA) Match Breakdown & Visual Coaching

Use this workflow to analyze match footage or event timelines from an authentic Caio Terra Association black-belt perspective ("Technique Conquers All": frames, exact grip geometry, 90-degree hip angles, and energy conservation) and produce timestamped curriculum references plus visual study media.

## Procedure

1. **Lock Athlete Identity & Verify Limb Geometry**
   - Set `locked_athlete` (`A` or `B`) and verify gi color, patches, and scoreboard row across key frames before writing technical cues.
   - Check exact left/right limbs on every grip, foot-on-hip frame, underhook, triangle lock, and armbar transition. Never guess left vs right without frame verification.

2. **Apply Core CTA Positional Diagnostics**
   - **Standing Guard Pull**: Establish collar + four-finger outer cuff grip and plant the foot squarely on the cuff-controlled hip crease before sitting close to the heel. Flag mid-air hop pulls at White Belt as immediate IBJJF Art. 6.2.2 DQ hazards.
   - **Closed Guard Posture & Attacks**: Replace static mid-back holding (which draws IBJJF stalling calls) with active posture breaks, cross-lapel overhook sequences (collar choke / triangle / pendulum dilemma), 2-on-1 arm drags paired with a near-leg underhook to flatten to Mount, or standing double-ankle sweeps.
   - **Triangle & Inside-Triangle Armbar Mechanics**: Isolate one wrist across the centerline with a four-finger cuff grip before uncrossing ankles to shoot a triangle. Cut 45 to 90 degrees grabbing your choking shin (never your own foot), curl the hamstring downward onto the neck, and for armbar finishes rotate the opponent's thumb upward while hugging the wrist high at the collar.
   - **Double-Under Stack Prevention**: Uncross ankles early, flare knees wide, drive locked-arm collar frames into the opponent's clavicles, or pivot to a 90-degree shoulder clamp before the hips get loaded past vertical.
   - **Seated Wrestle-Up**: Secure the ankle or pant cuff below the knee first with head inside (forehead glued to sternum) rather than reaching over the back with head outside.

3. **Attach Verified CTA Curriculum Timestamp Jump-Links**
   - Query `KnowledgeRetriever.recommend_cta_for_position(position, situation, belt)` or `GET /api/knowledge/search?corpus=cta&q=<query>` inside `/Users/korymath/Documents/code/rollmetrics`.
   - Include direct YouTube timestamp URLs (`https://www.youtube.com/watch?v=<id>&t=<sec>s`) from Caio Terra Modern Jiu-Jitsu and CTA Academy curriculum lessons.

4. **Render 3-Panel Pointer Boards & 0.4x Slow-Mo Clips**
   - Generate a 3-step freeze-frame pointer board (`t-1.5s`, `t`, `t+1.5s`) and smooth `0.4x` slow-motion clip for each decisive exchange:
     ```bash
     cd /Users/korymath/Documents/code/rollmetrics
     .venv/bin/rollmetrics media board \
       --video path/to/match.mp4 \
       --timestamp 154.0 \
       --output data/runtime/renders/study_board.png \
       --title "3-Step Closed Guard Armbar Chain" \
       --cue "Step 1: Drag tricep across centerline" \
       --cue "Step 2: Lock high diamond trap" \
       --cue "Step 3: Thumb-up wrist hug & downward hamstring bite"

     .venv/bin/rollmetrics media slowmo \
       --video path/to/match.mp4 \
       --start 148.0 \
       --duration 15.0 \
       --speed 0.4 \
       --output data/runtime/renders/study_slowmo.mp4
     ```
   - Confirm output files exist and render cleanly before sharing links or embedding in artifacts.

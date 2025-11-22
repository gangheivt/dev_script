/*************************************************************
SBC Example PLC ANSI-C Source Code
File: sbcplc.c
*************************************************************/
#include <math.h>
//#include "sbc.h"
#include "audio_msbc_plc.h"
#include "assert.h"
#include "stdlib.h"
#include "memory.h"

static void g711plc_scalespeech(LowcFE_c *, short *out);
static void g711plc_getfespeech(LowcFE_c *, short *out, int sz);
static void g711plc_savespeech(LowcFE_c *, short *s);
static int g711plc_findpitch(LowcFE_c *);
static void g711plc_overlapadd(Float *l, Float *r, Float *o, int cnt);
static void g711plc_overlapadds(short *l, short *r, short *o, int cnt);
static void g711plc_overlapaddatend(LowcFE_c *, short *s, short *f, int cnt);
static void g711plc_convertsf(short *f, Float *t, int cnt);
static void g711plc_convertfs(Float *f, short *t, int cnt);
static void g711plc_copyf(Float *f, Float *t, int cnt);
static void g711plc_copys(short *f, short *t, int cnt);
static void g711plc_zeros(short *s, int cnt);

/* 新增优化函数声明 */
#ifdef G711_ADAPTIVE_PLC
static void apply_perceptual_weight(short* frame, int size, LowcFE_c* lc, float alpha);
#endif
#ifdef NONLINEAR_ATTEN
static void nonlinear_attenuation(short* out, int sz, int erasecnt, int pitch);
#endif
#ifdef COMFORT_NOISE
static void generate_comfort_noise(ComfortNoiseGenerator* cng, short* out, int sz);
#endif
static void lpc_analysis(short* frame, int size, float* coeff);
static int enhanced_findpitch(LowcFE_c* lc);
static inline float compute_dynamic_alpha(float current_energy, float prev_energy, float prev_alpha);
static void dynamic_overlapaddatend(LowcFE_c* lc, short* s, short* f, int cnt);

int fading_count = G711_ATT_FADE_COUNT;
void msbc_g711plc_construct(LowcFE_c *lc)
{
    lc->pitch_min = 40 * 2;      /* minimum allowed pitch, 200 Hz */
    lc->pitch_max = 120 * 2;     /* maximum allowed pitch, 66 Hz */
    lc->pitchdiff = (lc->pitch_max - lc->pitch_min);
    lc->poverlapmax = (lc->pitch_max >> 2);        /* maximum pitch ola window */
    lc->historylen = (lc->pitch_max * 3 + lc->poverlapmax);   /* history buffer length */
    lc->ndec = 2;       /* 2:1 decimation */
    lc->corrlen = 160 * 2;     /* 20 msec correlation length */
    lc->corrbuflen = (lc->corrlen + lc->pitch_max);   /* correlation buffer length */
    lc->corrminpower = ((float)250. * 2);   /* minimum power */

    lc->eoverlapincr = 24 * 2;      /* end ola increment per frame, 3ms */
    lc->framesz = 60 * 2;      /* 7.5 msec at 8khz */

    lc->attenfac = ((float).2);     /* attenuation factor per 10ms frame */
    lc->attenincr = (lc->attenfac / lc->framesz);      /* attenuation per sample */
    assert(lc->historylen <= HISTORYLEN_MAX);
    assert(lc->poverlapmax <= POVERLAPMAX_);
    assert(lc->framesz <= FRAMESZ_MAX);

    lc->sbcrt = 36;     /* SBC reconvergence time*/

    lc->erasecnt = 0;
    lc->pitchbufend = &lc->pitchbuf[lc->historylen];
    g711plc_zeros(lc->history, lc->historylen);

    // 新增优化初始化
    lc->alpha = 0.75f;
    lc->prev_energy = -99.0f;
    lc->last_pitch = (lc->pitch_min + lc->pitch_max) / 2; // 默认基音
    memset(lc->cng.lpc_coeff, 0, sizeof(lc->cng.lpc_coeff));

#ifdef COMFORT_NOISE
    lc->cng.noise_floor = 300.0f; // 初始噪声基底
    lc->cng.hist_index = 0;
    memset(lc->cng.energy_history, 0, sizeof(lc->cng.energy_history));
#endif

}


void cvsd_g711plc_construct(LowcFE_c *lc)
{
    lc->pitch_min = 40;      /* minimum allowed pitch, 200 Hz */
    lc->pitch_max = 120;     /* maximum allowed pitch, 66 Hz */
    lc->pitchdiff = (lc->pitch_max - lc->pitch_min);
    lc->poverlapmax = (lc->pitch_max >> 2);        /* maximum pitch ola window */
    lc->historylen = (lc->pitch_max * 3 + lc->poverlapmax);   /* history buffer length */
    lc->ndec = 2;       /* 2:1 decimation */
    lc->corrlen = 160;     /* 20 msec correlation length */
    lc->corrbuflen = (lc->corrlen + lc->pitch_max);   /* correlation buffer length */
    lc->corrminpower = ((Float)250.);   /* minimum power */
    lc->eoverlapincr = 24;      /* end ola increment per frame, 3ms */
    lc->framesz = 60;      /* 7.5 msec at 8khz */

    lc->attenfac = ((Float)1.0/fading_count);     /* attenuation factor per 10ms frame */
    lc->attenincr = (lc->attenfac / lc->framesz);      /* attenuation per sample */
    assert(lc->historylen <= HISTORYLEN_MAX);
    assert(lc->poverlapmax <= POVERLAPMAX_);
    assert(lc->framesz <= FRAMESZ_MAX);

    lc->sbcrt = 0;      /* SBC reconvergence time*/

    lc->erasecnt = 0;
    lc->pitchbufend = &lc->pitchbuf[lc->historylen];
    g711plc_zeros(lc->history, lc->historylen);

    // 新增优化初始化
    lc->alpha = 0.75f;
    lc->prev_energy = -99.0f;
    lc->last_pitch = 80; // 默认基音周期
    memset(lc->cng.lpc_coeff, 0, sizeof(lc->cng.lpc_coeff));

#ifdef COMFORT_NOISE
    lc->cng.noise_floor = 500.0f; // 初始噪声基底
    lc->cng.hist_index = 0;
    memset(lc->cng.energy_history, 0, sizeof(lc->cng.energy_history));
#endif

}

static void lpc_analysis(short* frame, int size, float* coeff) {
    float autocorr[LPC_ORDER + 1] = { 0 };

    // 1. 计算自相关函数
    for (int lag = 0; lag <= LPC_ORDER; lag++) {
        for (int i = 0; i < size - lag; i++) {
            autocorr[lag] += (float)frame[i] * frame[i + lag];
        }
    }

    // 2. Levinson-Durbin递归
    float err = autocorr[0];
    coeff[0] = 1.0f;

    for (int k = 1; k <= LPC_ORDER; k++) {
        // 计算反射系数
        float lambda = 0.0f;
        for (int m = 1; m < k; m++) {
            lambda -= coeff[m] * autocorr[k - m];
        }
        lambda /= err;

        // 更新系数
        for (int n = k; n >= 1; n--) {
            coeff[n] += lambda * coeff[k - n];
        }
        err *= (1 - lambda * lambda);
    }
}

// 动态α计算（平滑过渡）
static inline float compute_dynamic_alpha(float current_energy, float prev_energy, float prev_alpha) {
    float target_alpha;

    if (current_energy < -30.0f && prev_energy < -30.0f)
        target_alpha = 0.65f;  // 低电平适当提升高频
    else if (current_energy > -10.0f && prev_energy > -10.0f)
        target_alpha = 0.85f;   // 高电平抑制噪声
    else
        target_alpha = 0.75f;  // 默认值

    // 每帧最大变化0.05，避免突变
    if (fabs(target_alpha - prev_alpha) > 0.05f) {
        if (target_alpha > prev_alpha)
            return prev_alpha + 0.05f;
        else
            return prev_alpha - 0.05f;
    }
    return target_alpha;
}

// 感知加权滤波（动态α）[3](@ref)
static void apply_perceptual_weight(short* frame, int size, LowcFE_c* lc, float alpha) {
    /* 基于LPC的前向加权滤波：W(z) = 1/(1 - α*A(z)) */
    for (int i = 0; i < size; i++) {
        float weighted = frame[i];

        // LPC加权滤波（8阶）
        for (int j = 1; j <= LPC_ORDER && i - j >= 0; j++) {
            weighted -= alpha * lc->cng.lpc_coeff[j] * frame[i - j];
        }

        // 限幅处理
        frame[i] = (short)fmaxf(fminf(weighted, 32767), -32768);
    }
}

#ifdef G711_ADAPTIVE_PLC
// 多候选基音检测（抗噪+连续性优化）
static int enhanced_findpitch(LowcFE_c* lc) {

    Float* l = lc->pitchbufend - lc->corrlen;  // 当前分析段
    Float* r = lc->pitchbufend - lc->corrbuflen; // 历史缓冲区
    PitchCandidate candidates[3] = { {-1e9, 0}, {-1e9, 0}, {-1e9, 0} };

    // === 粗搜索：以ndec步长遍历基音范围 ===
    for (int j = 0; j <= lc->pitchdiff; j += lc->ndec) {
        Float energy = 0.0f;
        Float corr = 0.0f;

        // 计算当前偏移的能量和互相关
        for (int i = 0; i < lc->corrlen; i++) {
            energy += r[j + i] * r[j + i];
            corr += r[j + i] * l[i];
        }

        // 归一化互相关（NCCF）抗噪处理[4](@ref)
        Float nccf = (corr * corr) / (energy + 1e-6f);

        // 更新Top3候选
        if (nccf > candidates[0].corr) {
            candidates[2] = candidates[1];
            candidates[1] = candidates[0];
            candidates[0] = (PitchCandidate){ nccf, j };
        }
        else if (nccf > candidates[1].corr) {
            candidates[2] = candidates[1];
            candidates[1] = (PitchCandidate){ nccf, j };
        }
        else if (nccf > candidates[2].corr) {
            candidates[2] = (PitchCandidate){ nccf, j };
        }
    }

    // === 细搜索：最佳候选附近精细扫描 ===
    int best_match = candidates[0].index;
    Float best_corr = candidates[0].corr;
    int search_start = best_match - (lc->ndec - 1);
    int search_end = best_match + (lc->ndec - 1);

    // 边界保护
    if (search_start < 0) search_start = 0;
    if (search_end > lc->pitchdiff) search_end = lc->pitchdiff;

    for (int j = search_start; j <= search_end; j++) {
        Float energy = 0.0f;
        Float corr = 0.0f;

        for (int i = 0; i < lc->corrlen; i++) {
            energy += r[j + i] * r[j + i];
            corr += r[j + i] * l[i];
        }

        Float nccf = (corr * corr) / (energy + 1e-6f);
        if (nccf > best_corr) {
            best_corr = nccf;
            best_match = j;
        }
    }

    // === 历史连续性校验（避免基音跳变） ===
    int final_pitch = lc->pitch_max - best_match;
    for (int i = 0; i < 3; i++) {
        int candidate_pitch = lc->pitch_max - candidates[i].index;
        // 偏差<5%历史基音则优先选择[1](@ref)
        if (abs(candidate_pitch - lc->last_pitch) < 0.05 * lc->last_pitch) {
            final_pitch = candidate_pitch;
            break;
        }
    }

    // 动态容差阈值：根据能量变化率调整
    float current_energy = 10 * log10f(lc->corrminpower + 1e-6f);
    float energy_diff = (float)fabs(current_energy - lc->prev_energy);
    float threshold = (energy_diff > 10.0f) ? 0.15f : 0.05f;  // 高动态段放宽至15%

    for (int i = 0; i < 3; i++) {
        int candidate_pitch = lc->pitch_max - candidates[i].index;
        // 使用动态阈值校验
        if (abs(candidate_pitch - lc->last_pitch) < threshold * lc->last_pitch) {
            final_pitch = candidate_pitch;
            break;
        }
    }

    lc->last_pitch = final_pitch;  // 更新历史基音
    return final_pitch;
}
#endif

// 非线性衰减（分段策略）[3](@ref)
#ifdef NONLINEAR_ATTEN
static void nonlinear_attenuation(short* out, int sz, int erasecnt, int pitch) {
    float g = 1.0f;
    if (erasecnt <= 5) {
        g = 1.0f - 0.02f * erasecnt;
    }
    else {
        g = 0.9f * powf(0.88f, (float)(erasecnt - 5)); // 减缓衰减速度
    }

    // 基频谐波补偿（抑制金属声）
    for (int i = 0; i < sz; i++) {
        float sample = out[i] * g;
        // 仅在基频有效时补偿（pitch>0）
        if (pitch > 0 && i % pitch < pitch / 4) {
            sample *= 1.1f; // 增强基频能量
        }
        out[i] = (short)fmaxf(fminf(sample, 32767), -32768);
    }
}
#endif


// 舒适噪声生成（LPC建模）[5](@ref)
#ifdef COMFORT_NOISE
static void generate_comfort_noise(ComfortNoiseGenerator* cng, short* out, int sz) {
    // 1. 从历史噪声中提取LPC系数
    lpc_analysis(out, sz, cng->lpc_coeff); // 使用当前缓冲区分析

    // 2. 生成高斯白噪声
    for (int i = 0; i < sz; i++) {
        float noise = ((rand() / (float)RAND_MAX) * 2.0f - 1.0f) * cng->noise_floor;

        // 3. LPC滤波重构背景声
        for (int j = 1; j < LPC_ORDER; j++) {
            if (i >= j) noise += cng->lpc_coeff[j] * out[i - j];
        }
        out[i] = (short)(noise * CNG_GAIN_SCALE);
    }
}
#endif


// 动态OLA窗口（相位对齐）[1](@ref)
static void dynamic_overlapaddatend(LowcFE_c* lc, short* s, short* f, int cnt) {
    // 根据丢包时长扩展窗口（最长20ms）
    int dynamic_olen = lc->poverlap + (lc->erasecnt * 8);
    if (dynamic_olen > cnt) dynamic_olen = cnt;
    if (dynamic_olen > 160) dynamic_olen = 160; // 20ms限制

    // 执行带相位对齐的OLA
    Float incr = (Float)1.0 / (dynamic_olen - lc->sbcrt);
    Float gain = (Float)1.0 - (lc->erasecnt - 1) * lc->attenfac;
    if (gain < 0.) gain = (Float)0.;
    Float incrg = incr * gain;
    Float lw = ((Float)1.0 - incr) * gain;
    Float rw = incr;

    // 起始段：完全使用合成信号
    for (int i = 0; i < lc->sbcrt; i++) {
        Float t = gain * f[i];
        s[i] = (short)t;
    }

    // 重叠段：渐变混合
    for (int i = lc->sbcrt; i < dynamic_olen; i++) {
        Float t = lw * f[i] + rw * s[i];
        s[i] = (short)fmaxf(fminf(t, 32767.0f), -32768.0f);
        lw -= incrg;
        rw += incr;
    }
}

/*
 * Get samples from the circular pitch buffer. Update poffset so
 * when subsequent frames are erased the signal continues.
 */
static void g711plc_getfespeech(LowcFE_c *lc, short *out, int sz)
{
    while (sz)
    {
        int cnt = lc->pitchblen - lc->poffset;
        if (cnt > sz)
            cnt = sz;
        g711plc_convertfs(&lc->pitchbufstart[lc->poffset], out, cnt);
        lc->poffset += cnt;
        if (lc->poffset == lc->pitchblen)
            lc->poffset = 0;
        out += cnt;
        sz -= cnt;
    }
}

static void g711plc_scalespeech(LowcFE_c *lc, short *out)
{
    int i;
    Float g = (Float) 1. - (lc->erasecnt - 1) * lc->attenfac;
    for (i = 0; i < lc->framesz; i++)
    {
        out[i] = (short)(out[i] * g);
        g -= lc->attenincr;
    }
}

/*
 * Generate the synthetic signal.
 * At the beginning of an erasure determine the pitch, and extract
 * one pitch period from the tail of the signal. Do an OLA for 1/4
 * of the pitch to smooth the signal. Then repeat the extracted signal
 * for the length of the erasure. If the erasure continues for more than
 * 10 msec, increase the number of periods in the pitchbuffer. At the end
 * of an erasure, do an OLA with the start of the first good frame.
 * The gain decays as the erasure gets longer.
 */
void g711plc_dofe(LowcFE_c *lc, short *out)
{
    float current_energy = 10 * log10f(lc->corrminpower + 1e-6f);

    if (lc->erasecnt == 0)
    {
        /* get history */
        g711plc_convertsf(lc->history, lc->pitchbuf, lc->historylen);
#ifdef G711_ADAPTIVE_PLC
        lc->pitch = enhanced_findpitch(lc); /* find pitch */
#else
        lc->pitch = g711plc_findpitch(lc);
#endif
        lc->poverlap = lc->pitch >> 2;      /* OLA 1/4 wavelength */
        /* save original last poverlap samples */
        g711plc_copyf(lc->pitchbufend - lc->poverlap, lc->lastq, lc->poverlap);
        lc->poffset = 0;            /* create pitch buffer with 1 period */
        lc->pitchblen = lc->pitch;
        lc->pitchbufstart = lc->pitchbufend - lc->pitchblen;
        g711plc_overlapadd(lc->lastq, lc->pitchbufstart - lc->poverlap, lc->pitchbufend - lc->poverlap, lc->poverlap);
        /* update last 1/4 wavelength in history buffer */
        g711plc_convertfs(lc->pitchbufend - lc->poverlap, &lc->history[lc->historylen - lc->poverlap], lc->poverlap);
        /* get synthesized speech */
        g711plc_getfespeech(lc, out, lc->framesz);
        // 记录当前能量用于动态α
        lc->cng.energy_history[lc->cng.hist_index] = current_energy;
        lc->cng.hist_index = (lc->cng.hist_index + 1) % NOISE_HISTORY;
    }
#ifdef COMFORT_NOISE
    else if (lc->erasecnt > COMFORT_NOISE_START) {
        // 长丢包切换舒适噪声
        generate_comfort_noise(&lc->cng, out, lc->framesz);
    }
#else
    else if (lc->erasecnt == 1 || lc->erasecnt == 2)
    {
        /* tail of previous pitch estimate */
        short tmp[POVERLAPMAX_];
        int saveoffset = lc->poffset;       /* save offset for OLA */
        /* continue with old pitchbuf */
        g711plc_getfespeech(lc, tmp, lc->poverlap);
        /* add periods to the pitch buffer */
        lc->poffset = saveoffset;
        while (lc->poffset > lc->pitch)
            lc->poffset -= lc->pitch;
        lc->pitchblen += lc->pitch; /* add a period */
        lc->pitchbufstart = lc->pitchbufend - lc->pitchblen;
        g711plc_overlapadd(lc->lastq, lc->pitchbufstart - lc->poverlap, lc->pitchbufend - lc->poverlap, lc->poverlap);
        /* overlap add old pitchbuffer with new */
        g711plc_getfespeech(lc, out, lc->framesz);
        g711plc_overlapadds(tmp, out, out, lc->poverlap);
        g711plc_scalespeech(lc, out);
    }
    else if (lc->erasecnt > fading_count)
    {
        g711plc_zeros(out, lc->framesz);
    }
#endif
    else
    {
        // 常规合成帧处理
        g711plc_getfespeech(lc, out, lc->framesz);

#ifdef G711_ADAPTIVE_PLC
        float energy_diff = (float)fabs(current_energy - lc->prev_energy);
        // 能量突变>10dB时跳过自适应处理（保护瞬态信号）
        if (energy_diff < 10.0f) {
            lc->alpha = compute_dynamic_alpha(current_energy, lc->prev_energy, lc->alpha);
            apply_perceptual_weight(out, lc->framesz, lc, lc->alpha);
        }
#endif

#ifdef NONLINEAR_ATTEN
        // 非线性衰减替代线性衰减
        nonlinear_attenuation(out, lc->framesz, lc->erasecnt, lc->pitch);
#else
        // 保留原始衰减
        g711plc_scalespeech(lc, out);
#endif
    }
 
    /* 新增：CVSD模式动态加权处理 */
    lc->prev_energy = current_energy;
    lc->erasecnt++;
    g711plc_savespeech(lc, out);
}

/*
 * Save a frames worth of new speech in the history buffer.
 * Return the output speech delayed by POVERLAPMAX.
 */
static void g711plc_savespeech(LowcFE_c *lc, short *s)
{
    /* make room for new signal */
    g711plc_copys(&lc->history[lc->framesz], lc->history, lc->historylen - lc->framesz);
    /* copy in the new frame */
    g711plc_copys(s, &lc->history[lc->historylen - lc->framesz], lc->framesz);
    /* copy out the delayed frame */
    g711plc_copys(&lc->history[lc->historylen - lc->framesz - lc->poverlapmax], s, lc->framesz);
}

/*
 * A good frame was received and decoded.
 * If right after an erasure, do an overlap add with the synthetic signal.
 * Add the frame to history buffer.
 */
void g711plc_addtohistory(LowcFE_c *lc, short *s)
{
    if (lc->erasecnt)
    {
        short overlapbuf[FRAMESZ_MAX];
        /*
         * longer erasures require longer overlaps
         * to smooth the transition between the synthetic
         * and real signal.
         */
        int olen = lc->poverlap + (lc->erasecnt + 1 - 1) * lc->eoverlapincr + lc->sbcrt;
        if (olen > lc->framesz)
            olen = lc->framesz;
        g711plc_getfespeech(lc, overlapbuf, olen);
        g711plc_overlapaddatend(lc, s, overlapbuf, olen);
        lc->erasecnt = 0;
    }
#if defined(COMFORT_NOISE)
    // 正常帧更新LPC系数
    lpc_analysis(s, lc->framesz, lc->cng.lpc_coeff);
#endif
    g711plc_savespeech(lc, s);
}

/*
 * Overlapp add the end of the erasure with the start of the first good frame
 * Scale the synthetic speech by the gain factor before the OLA.
 */
static void g711plc_overlapaddatend(LowcFE_c *lc, short *s, short *f, int cnt)
{
    int i;
    Float incrg;
    Float lw, rw;
    Float t;
    Float incr = (Float) 1. / (cnt - lc->sbcrt);
    Float gain = (Float) 1. - (lc->erasecnt - 1) * lc->attenfac;
    if (gain < 0.)
        gain = (Float) 0.;
    incrg = incr * gain;
    lw = ((Float) 1. - incr) * gain;
    rw = incr;

    for (i = 0; i < lc->sbcrt; i++)
    {
        t = gain * f[i];
        s[i] = (short)t;
    }

    for (i = lc->sbcrt; i < cnt; i++)
    {
        t = lw * f[i] + rw * s[i];
        if (t > 32767.)
            t = (Float) 32767.;
        else if (t < -32768.)
            t = (Float) - 32768.;
        s[i] = (short)t;
        lw -= incrg;
        rw += incr;
    }
}

/*
 * Overlapp add left and right sides
 */
static void g711plc_overlapadd(Float *l, Float *r, Float *o, int cnt)
{
    int i;
    Float incr, lw, rw, t;

    if (cnt == 0)
        return;
    incr = (Float) 1. / cnt;
    lw = (Float) 1. - incr;
    rw = incr;
    for (i = 0; i < cnt; i++)
    {
        t = lw * l[i] + rw * r[i];
        if (t > (Float) 32767.)
            t = (Float) 32767.;
        else if (t < (Float) - 32768.)
            t = (Float) - 32768.;
        o[i] = t;
        lw -= incr;
        rw += incr;
    }
}

/*
 * Overlapp add left and right sides
 */
static void g711plc_overlapadds(short *l, short *r, short *o, int cnt)
{
    int i;
    Float incr, lw, rw, t;

    if (cnt == 0)
        return;
    incr = (Float) 1. / cnt;
    lw = (Float) 1. - incr;
    rw = incr;
    for (i = 0; i < cnt; i++)
    {
        t = lw * l[i] + rw * r[i];
        if (t > (Float) 32767.)
            t = (Float) 32767.;
        else if (t < (Float) - 32768.)
            t = (Float) - 32768.;
        o[i] = (short)t;
        lw -= incr;
        rw += incr;
    }
}

/*
 * Estimate the pitch.
 * l - pointer to first sample in last 20 msec of speech.
 * r - points to the sample PITCH_MAX before l
 */
static int g711plc_findpitch(LowcFE_c *lc)
{
    int i, j, k;
    int bestmatch;
    Float bestcorr;
    Float corr;                   /* correlation */
    Float energy;                 /* running energy */
    Float scale;                  /* scale correlation by average power */
    Float *rp;                    /* segment to match */
    Float *l = lc->pitchbufend - lc->corrlen;
    Float *r = lc->pitchbufend - lc->corrbuflen;

    /* coarse search */
    rp = r;
    energy = (Float) 0.;
    corr = (Float) 0.;
    for (i = 0; i < lc->corrlen; i += lc->ndec)
    {
        energy += rp[i] * rp[i];
        corr += rp[i] * l[i];
    }
    scale = energy;
    if (scale < lc->corrminpower)
        scale = lc->corrminpower;
    corr = corr / (Float)sqrt(scale);
    bestcorr = corr;
    bestmatch = 0;
    for (j = lc->ndec; j <= lc->pitchdiff; j += lc->ndec)
    {
        energy -= rp[0] * rp[0];
        energy += rp[lc->corrlen] * rp[lc->corrlen];
        rp += lc->ndec;
        corr = 0.f;
        for (i = 0; i < lc->corrlen; i += lc->ndec)
            corr += rp[i] * l[i];
        scale = energy;
        if (scale < lc->corrminpower)
            scale = lc->corrminpower;
        corr /= (Float)sqrt(scale);
        if (corr >= bestcorr)
        {
            bestcorr = corr;
            bestmatch = j;
        }
    }
    /* fine search */
    j = bestmatch - (lc->ndec - 1);
    if (j < 0)
        j = 0;
    k = bestmatch + (lc->ndec - 1);
    if (k > lc->pitchdiff)
        k = lc->pitchdiff;
    rp = &r[j];
    energy = 0.f;
    corr = 0.f;
    for (i = 0; i < lc->corrlen; i++)
    {
        energy += rp[i] * rp[i];
        corr += rp[i] * l[i];
    }
    scale = energy;
    if (scale < lc->corrminpower)
        scale = lc->corrminpower;
    corr = corr / (Float)sqrt(scale);
    bestcorr = corr;
    bestmatch = j;
    for (j++; j <= k; j++)
    {
        energy -= rp[0] * rp[0];
        energy += rp[lc->corrlen] * rp[lc->corrlen];
        rp++;
        corr = 0.f;
        for (i = 0; i < lc->corrlen; i++)
            corr += rp[i] * l[i];
        scale = energy;
        if (scale < lc->corrminpower)
            scale = lc->corrminpower;
        corr = corr / (Float)sqrt(scale);
        if (corr > bestcorr)
        {
            bestcorr = corr;
            bestmatch = j;
        }
    }

    return lc->pitch_max - bestmatch;
}

static void g711plc_convertsf(short *f, Float *t, int cnt)
{
    int i;
    for (i = 0; i < cnt; i++)
        t[i] = (Float)f[i];
}

static void g711plc_convertfs(Float *f, short *t, int cnt)
{
    int i;
    for (i = 0; i < cnt; i++)
        t[i] = (short)f[i];
}

static void g711plc_copyf(Float *f, Float *t, int cnt)
{
    int i;
    for (i = 0; i < cnt; i++)
        t[i] = f[i];
}

static void g711plc_copys(short *f, short *t, int cnt)
{
    int i;
    for (i = 0; i < cnt; i++)
        t[i] = f[i];
}

static void g711plc_zeros(short *s, int cnt)
{
    int i;
    for (i = 0; i < cnt; i++)
        s[i] = 0;
}


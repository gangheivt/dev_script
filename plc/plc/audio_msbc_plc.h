/*
  ============================================================================
   File: lowcfe.h                                            V.1.0-24.MAY-2005
  ============================================================================
                     UGST/ITU-T G711 Appendix I PLC MODULE
                          GLOBAL FUNCTION PROTOTYPES
   History:
   24.May.05    v1.0    First version <AT&T>
                        Integration in STL2005 <Cyril Guillaume & Stephane Ragot - stephane.ragot@francetelecom.com>
  ============================================================================
*/
#ifndef __MSBC_LOWCFE_C_H__
#define __MSBC_LOWCFE_C_H__

#ifdef __cplusplus
extern "C" {
#endif

#ifdef USEDOUBLES
typedef double Float;         /* likely to be bit-exact between machines */
#else
typedef float Float;
#endif

#define HISTORYLEN_MAX  780
#define POVERLAPMAX_    60
#define FRAMESZ_MAX     120

#define G711_ATT_FADE_COUNT   10
// 改进功能开关
// #define G711_ADAPTIVE_PLC    // 启用自适应感知加权
#define COMFORT_NOISE        // 启用舒适噪声生成
#define NONLINEAR_ATTEN      // 启用非线性衰减

// 新增常量定义
#define LPC_ORDER           8
#define NOISE_HISTORY       32   // 噪声分析历史帧数
#define COMFORT_NOISE_START 30   // 300ms后启用舒适噪声（30帧*10ms）
#define CNG_GAIN_SCALE      0.2f // 舒适噪声增益缩放因子


// 舒适噪声生成器结构体 [5](@ref)
typedef struct {
    float lpc_coeff[LPC_ORDER];   // LPC系数
    float energy_history[NOISE_HISTORY]; // 历史能量缓存
    float noise_floor;            // 噪声基底能量
    int hist_index;               // 历史缓存索引
} ComfortNoiseGenerator;

// 基音候选结构（多候选检测）
typedef struct {
    float corr;
    int index;
} PitchCandidate;

typedef struct _LowcFE_c
{
    int pitch_min;
    int pitch_max;
    int pitchdiff;
    int poverlapmax;
    int historylen;
    int ndec;
    int corrlen;
    int corrbuflen;
    Float corrminpower;
    int eoverlapincr;
    int framesz;
    Float attenfac;
    Float attenincr;
    int erasecnt;               /* consecutive erased frames */
    int poverlap;               /* overlap based on pitch */
    int poffset;                /* offset into pitch period */
    int pitch;                  /* pitch estimate */
    int pitchblen;              /* current pitch buffer length */
    int sbcrt;                  /* SBC reconvergence time*/
    Float *pitchbufend;         /* end of pitch buffer */
    Float *pitchbufstart;       /* start of pitch buffer */
    Float pitchbuf[HISTORYLEN_MAX]; /* buffer for cycles of speech */
    Float lastq[POVERLAPMAX_];   /* saved last quarter wavelengh */
    short history[HISTORYLEN_MAX];  /* history buffer */

    float alpha;                  // 动态感知加权系数 [3](@ref)
    float prev_energy;            // 前一帧能量(dB)
    int last_pitch;               // 历史基音周期（连续性校验）
    ComfortNoiseGenerator cng;    // 舒适噪声生成器
} LowcFE_c;

/* public functions */
void msbc_g711plc_construct(LowcFE_c *);  /* constructor */
void cvsd_g711plc_construct(LowcFE_c *);  /* constructor */
void g711plc_dofe(LowcFE_c *, short *s);     /* synthesize speech for erasure */
void g711plc_addtohistory(LowcFE_c *, short *s);
/* add a good frame to history buffer */

#ifdef __cplusplus
}
#endif
#endif                          /* __MSBC_LOWCFE_C_H__ */
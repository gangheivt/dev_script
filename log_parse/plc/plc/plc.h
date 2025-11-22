#ifndef PLC_OPTIMIZED_H
#define PLC_OPTIMIZED_H

#include <stdint.h>
#include <stdbool.h>

// 常量定义（根据实际场景调整）
#define FRAME_SIZE 60          // 帧大小（8kHz采样率下7.5ms）
#define SAMPLE_RATE 8000        // 采样率（Hz）
#define MAX_LPC_ORDER 12        // 最大LPC阶数（动态调整）
#define MIN_LPC_ORDER 4         // 最小LPC阶数
#define BARK_BANDS 24           // Bark频带数量（匹配人耳感知）
#define PITCH_MIN 20            // 最小基音周期（50Hz）
#define PITCH_MAX 160           // 最大基音周期（62.5Hz）
#define CROSSFADE_LEN 10        // 帧间交叉淡化长度（样本数）

/* FFT Handler (common interface) */
typedef struct {
#ifdef USE_ARM_DSP_FFT
    // ARM CMSIS-DSP FFT specific data
    const arm_cfft_instance_f32* instance;  // CMSIS FFT instance
    uint32_t size;                          // FFT size
#else
    // Native FFT specific data
    uint32_t size;                          // FFT size
    float* twiddle_real;                    // Twiddle factors (real)
    float* twiddle_imag;                    // Twiddle factors (imaginary)
#endif
} FFTHandler;
#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

// 音频帧结构
typedef struct {
    int16_t pcm[FRAME_SIZE];    // 时域PCM数据
    float lpc_coeffs[MAX_LPC_ORDER + 1];  // LPC系数（0阶为1.0）
    int pitch_period;           // 基音周期
    bool is_unvoiced;           // 清浊音标记
    float energy;               // 帧能量
} AudioFrame;

// 函数声明
void compute_lpc(const int16_t* samples, float* lpc_coeffs, int* optimal_order, bool is_unvoiced);
int find_pitch_period(const int16_t* samples, int prev_period);
bool is_unvoiced(const int16_t* frame, float* spectral_flatness);
void add_comfort_noise(int16_t* pcm, const AudioFrame* history);
void noise_shaping(int16_t* pcm_frame, FFTHandler* fft_handler, const AudioFrame* history);
void conceal_lost_frame(AudioFrame* output, const AudioFrame* history, int loss_count);

FFTHandler* fft_init(int size);
void fft_execute(FFTHandler* handler, float* buffer, bool inverse);
void fft_cleanup(FFTHandler* handler);

#endif // PLC_OPTIMIZED_H

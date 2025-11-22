#include "plc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdbool.h>

// Bark频带边界（Hz），匹配人耳感知范围
static const float bark_bands[BARK_BANDS + 1] = {
    0,100,200,300,400,510,630,770,920,1080,1270,1480,
    1720,2000,2320,2700,3150,3700,4400,5300,6400,7700,9500,12000,15500
};

// 人耳绝对听阈（dB SPL，用于噪声掩蔽修正）
static const float hearing_threshold[BARK_BANDS] = {
    30, 20, 15, 10, 5, 0, -5, -5, -5, -5, -5, -5,
    0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55
};

// 工具函数：频率（Hz）转Bark
static float hz_to_bark(float hz) {
    if (hz < 0) return 0.0f;
    return 13.0f * atanf(0.00076f * hz) + 3.5f * atanf(powf(hz / 7500.0f, 2));
}

// 工具函数：获取频率对应的Bark带
static int get_bark_band(float hz) {
    for (int b = 0; b < BARK_BANDS; b++) {
        if (hz >= bark_bands[b] && hz < bark_bands[b + 1]) {
            return b;
        }
    }
    return BARK_BANDS - 1;
}

// 工具函数：限制浮点数范围
static float clampf(float x, float min, float max) {
    return (x < min) ? min : (x > max) ? max : x;
}

// 工具函数：计算帧能量
static float frame_energy(const int16_t* frame) {
    float energy = 0.0f;
    for (int i = 0; i < FRAME_SIZE; i++) {
        energy += powf(frame[i], 2);
    }
    return energy / FRAME_SIZE;
}


// 1. LPC系数计算（动态阶数，适配清浊音）
void compute_lpc(const int16_t* samples, float* lpc_coeffs, int* optimal_order, bool is_unvoiced) {
    // 清浊音自适应最大阶数（清音用低阶避免过拟合噪声）
    int max_order = is_unvoiced ? 6 : MAX_LPC_ORDER;
    float autocorr[MAX_LPC_ORDER + 1] = { 0 };

    // 计算自相关函数
    for (int i = 0; i <= max_order; i++) {
        for (int j = 0; j < FRAME_SIZE - i; j++) {
            autocorr[i] += samples[j] * samples[j + i];
        }
    }
    if (autocorr[0] < 1e-6) {  // 避免零能量帧除零
        memset(lpc_coeffs, 0, sizeof(float) * (max_order + 1));
        lpc_coeffs[0] = 1.0f;
        *optimal_order = MIN_LPC_ORDER;
        return;
    }

    // Levinson-Durbin算法求解LPC系数
    float error = autocorr[0];
    lpc_coeffs[0] = 1.0f;
    *optimal_order = max_order;

    for (int i = 1; i <= max_order; i++) {
        float reflection = -autocorr[i];
        for (int j = 1; j < i; j++) {
            reflection -= lpc_coeffs[j] * autocorr[i - j];
        }
        reflection /= error;

        // 更新系数
        lpc_coeffs[i] = reflection;
        for (int j = 1; j <= i / 2; j++) {
            float tmp = lpc_coeffs[j];
            lpc_coeffs[j] += reflection * lpc_coeffs[i - j];
            lpc_coeffs[i - j] += reflection * tmp;
        }

        // 误差更新与阶数截断（基于归一化误差）
        error *= (1.0f - reflection * reflection);
        float norm_error = error / autocorr[0];
        // 清音允许更高误差（0.1），浊音更严格（0.05）
        float threshold = is_unvoiced ? 0.1f : 0.05f;
        if (i >= MIN_LPC_ORDER && norm_error < threshold) {
            *optimal_order = i;
            break;
        }
    }
}


// 2. 基音周期估计（鲁棒性优化：倒谱+时间平滑）
int find_pitch_period(const int16_t* samples, int prev_period) {
    // 步骤1：预处理（去直流+预加重）
    int16_t preprocessed[FRAME_SIZE];
    for (int i = 0; i < FRAME_SIZE; i++) {
        preprocessed[i] = samples[i] - 0.97f * (i > 0 ? samples[i - 1] : 0);
    }

    // 步骤2：计算互相关（带倒谱加权抑制噪声）
    float max_corr = -1.0f;
    int best_period = prev_period;  // 初始化为历史基音（平滑用）
    float corr[PITCH_MAX - PITCH_MIN + 1] = { 0 };

    for (int p = PITCH_MIN; p <= PITCH_MAX; p++) {
        float c = 0.0f;
        for (int i = 0; i < FRAME_SIZE - p; i++) {
            c += preprocessed[i] * preprocessed[i + p];
        }
        corr[p - PITCH_MIN] = c;
        if (c > max_corr) {
            max_corr = c;
            best_period = p;
        }
    }

    // 步骤3：倒谱滤波（抑制谐波干扰）
    float cepstrum[PITCH_MAX - PITCH_MIN + 1] = { 0 };
    for (int p = PITCH_MIN; p <= PITCH_MAX; p++) {
        cepstrum[p - PITCH_MIN] = logf(fabsf(corr[p - PITCH_MIN]) + 1e-6);
    }
    // 低通滤波（保留基音周期信息）
    for (int p = PITCH_MIN + 1; p < PITCH_MAX; p++) {
        cepstrum[p - PITCH_MIN] = 0.3f * cepstrum[p - PITCH_MIN - 1] +
            0.4f * cepstrum[p - PITCH_MIN] +
            0.3f * cepstrum[p - PITCH_MIN + 1];
    }

    // 步骤4：重新找最大峰值（基于倒谱）
    max_corr = -1.0f;
    for (int p = PITCH_MIN; p <= PITCH_MAX; p++) {
        if (cepstrum[p - PITCH_MIN] > max_corr) {
            max_corr = cepstrum[p - PITCH_MIN];
            best_period = p;
        }
    }

    // 步骤5：时间平滑（与历史基音加权融合，避免突变）
    return (int)(0.7f * best_period + 0.3f * prev_period);
}


// 3. 清浊音判断（多特征融合：过零率+频谱平坦度+能量）
bool is_unvoiced(const int16_t* frame, float* spectral_flatness) {
    // 特征1：过零率
    int zero_cross = 0;
    for (int i = 1; i < FRAME_SIZE; i++) {
        if ((frame[i - 1] > 0 && frame[i] < 0) || (frame[i - 1] < 0 && frame[i] > 0)) {
            zero_cross++;
        }
    }
    float zcr_norm = (float)zero_cross / FRAME_SIZE;

    // 特征2：能量
    float energy = frame_energy(frame);

    // 特征3：频谱平坦度（越高越可能是清音）
    FFTHandler* fft = fft_init(FRAME_SIZE);
    float fft_buf[2 * FRAME_SIZE] = { 0 };
    for (int i = 0; i < FRAME_SIZE; i++) {
        fft_buf[2 * i] = frame[i] / 32768.0f;  // 归一化
    }
    fft_execute(fft, fft_buf, false);

    float geo_mean = 0.0f, arith_mean = 0.0f;
    for (int k = 1; k < FRAME_SIZE / 2; k++) {  // 跳过直流分量
        float mag = sqrtf(fft_buf[2 * k] * fft_buf[2 * k] + fft_buf[2 * k + 1] * fft_buf[2 * k + 1]);
        geo_mean += logf(mag + 1e-6);  // 几何平均（对数和）
        arith_mean += mag;             // 算术平均
    }
    geo_mean = expf(geo_mean / (FRAME_SIZE / 2 - 1));
    arith_mean /= (FRAME_SIZE / 2 - 1);
    *spectral_flatness = (arith_mean < 1e-6) ? 0.0f : (geo_mean / arith_mean);

    fft_cleanup(fft);

    // 多特征判决（自适应阈值）
    bool high_zcr = zcr_norm > 0.25f;  // 过零率高
    bool low_energy = energy < 800.0f; // 能量低（动态调整）
    bool flat_spectrum = *spectral_flatness > 0.6f;  // 频谱平坦

    return high_zcr && (low_energy || flat_spectrum);
}


// 4. 舒适噪声生成（与历史噪声频谱匹配）
void add_comfort_noise(int16_t* pcm, const AudioFrame* history) {
    // 估计历史帧噪声频谱（LPC残差）
    float noise_spectrum[BARK_BANDS] = { 0 };
    int lpc_order;
    float lpc_coeffs[MAX_LPC_ORDER + 1];
    compute_lpc(history->pcm, lpc_coeffs, &lpc_order, history->is_unvoiced);

    // 计算LPC残差（噪声近似）
    int16_t residual[FRAME_SIZE] = { 0 };
    for (int i = 0; i < FRAME_SIZE; i++) {
        float pred = 0.0f;
        for (int k = 1; k <= lpc_order; k++) {
            pred += lpc_coeffs[k] * (i >= k ? history->pcm[i - k] : 0);
        }
        residual[i] = history->pcm[i] - (int16_t)pred;
    }

    // 计算残差的Bark带能量
    FFTHandler* fft = fft_init(FRAME_SIZE);
    float fft_buf[2 * FRAME_SIZE] = { 0 };
    for (int i = 0; i < FRAME_SIZE; i++) {
        fft_buf[2 * i] = residual[i] / 32768.0f;
    }
    fft_execute(fft, fft_buf, false);

    float bin_hz = (SAMPLE_RATE / 2.0f) / (FRAME_SIZE / 2.0f);
    int band_count[BARK_BANDS] = { 0 };
    for (int k = 0; k < FRAME_SIZE / 2; k++) {
        float freq = k * bin_hz;
        int band = get_bark_band(freq);
        float mag = sqrtf(fft_buf[2 * k] * fft_buf[2 * k] + fft_buf[2 * k + 1] * fft_buf[2 * k + 1]);
        noise_spectrum[band] += mag * mag;
        band_count[band]++;
    }
    // 归一化到每个频带的平均能量
    for (int b = 0; b < BARK_BANDS; b++) {
        if (band_count[b] > 0) {
            noise_spectrum[b] /= band_count[b];
        }
    }
    fft_cleanup(fft);

    // 生成匹配频谱的噪声（通过FFT合成）
    float noise_fft[2 * FRAME_SIZE] = { 0 };
    for (int k = 0; k < FRAME_SIZE / 2; k++) {
        float freq = k * bin_hz;
        int band = get_bark_band(freq);
        // 噪声幅度匹配历史噪声频谱
        float amp = sqrtf(noise_spectrum[band] * 0.1f);  // 衰减系数控制强度
        float phase = 2 * M_PI * ((float)rand() / RAND_MAX);  // 随机相位
        noise_fft[2 * k] = amp * cosf(phase);
        noise_fft[2 * k + 1] = amp * sinf(phase);
    }
    // 对称填充（实信号）
    for (int k = FRAME_SIZE / 2 + 1; k < FRAME_SIZE; k++) {
        noise_fft[2 * k] = noise_fft[2 * (FRAME_SIZE - k)];
        noise_fft[2 * k + 1] = -noise_fft[2 * (FRAME_SIZE - k) + 1];
    }

    // IFFT转换为时域噪声
    FFTHandler* ifft = fft_init(FRAME_SIZE);
    fft_execute(ifft, noise_fft, true);
    for (int i = 0; i < FRAME_SIZE; i++) {
        pcm[i] += (int16_t)clampf(noise_fft[2 * i] * 32767.0f, -32768.0f, 32767.0f);
    }
    fft_cleanup(ifft);
}


// 5. 噪声整形（基于人耳掩蔽效应优化）
void noise_shaping(int16_t* pcm_frame, FFTHandler* fft_handler, const AudioFrame* history) {
    int fft_size = FRAME_SIZE;
    float fft_buf[2 * FRAME_SIZE] = { 0 };
    float window[FRAME_SIZE];

    // 汉宁窗（减少频谱泄漏）
    for (int i = 0; i < fft_size; i++) {
        window[i] = 0.5f - 0.5f * cosf(2 * M_PI * i / (fft_size - 1));
    }

    // 加窗并归一化
    for (int i = 0; i < fft_size; i++) {
        fft_buf[2 * i] = window[i] * (pcm_frame[i] / 32768.0f);
    }

    // 正向FFT
    fft_execute(fft_handler, fft_buf, false);

    // 计算幅度谱与Bark带能量
    float magnitude[FRAME_SIZE / 2];
    float bin_hz = (SAMPLE_RATE / 2.0f) / (fft_size / 2.0f);
    float band_energy[BARK_BANDS] = { 0 };
    int band_count[BARK_BANDS] = { 0 };

    for (int k = 0; k < fft_size / 2; k++) {
        float real = fft_buf[2 * k];
        float imag = fft_buf[2 * k + 1];
        magnitude[k] = sqrtf(real * real + imag * imag);
        float freq = k * bin_hz;
        int band = get_bark_band(freq);
        band_energy[band] += magnitude[k] * magnitude[k];
        band_count[band]++;
    }

    // 计算掩蔽阈值（修正人耳听阈）
    float masking_threshold[BARK_BANDS] = { 0 };
    for (int b = 0; b < BARK_BANDS; b++) {
        if (band_count[b] == 0) continue;
        float avg_energy = band_energy[b] / band_count[b];
        float band_db = 10 * log10f(avg_energy + 1e-12f);
        // 扩散函数：低频掩蔽更强（2dB/Bark），高频稍弱（4dB/Bark）
        float spread_factor = (b < 10) ? 2.0f : 4.0f;

        for (int b_adj = 0; b_adj < BARK_BANDS; b_adj++) {
            float dist = fabsf(b - b_adj);
            float spread_db = -spread_factor * dist;
            // 叠加并修正听阈
            masking_threshold[b_adj] += powf(10.0f, (band_db + spread_db - hearing_threshold[b_adj]) / 10.0f);
        }
    }

    // 应用噪声整形（限制频谱在掩蔽阈值内）
    for (int k = 0; k < fft_size / 2; k++) {
        float freq = k * bin_hz;
        int band = get_bark_band(freq);
        float threshold = sqrtf(masking_threshold[band] + 1e-12f);
        if (magnitude[k] > threshold) {
            float gain = threshold / magnitude[k];
            fft_buf[2 * k] *= gain;
            fft_buf[2 * k + 1] *= gain;
        }
    }

    // 逆向FFT与信号重构
    fft_execute(fft_handler, fft_buf, true);
    for (int i = 0; i < fft_size; i++) {
        float recovered = fft_buf[2 * i] / window[i];  // 逆窗处理
        pcm_frame[i] = (int16_t)clampf(recovered * 32767.0f, -32768.0f, 32767.0f);
    }
}


// 6. 丢包隐藏核心逻辑（自适应融合+帧间平滑）
void conceal_lost_frame(AudioFrame* output, const AudioFrame* history, int loss_count) {
    // 步骤1：估计当前帧属性（基于历史）
    float spectral_flatness;
    output->is_unvoiced = is_unvoiced(history->pcm, &spectral_flatness);
    output->energy = history->energy * powf(0.9f, loss_count);  // 能量衰减（平缓）

    // 步骤2：计算LPC系数与基音周期
    int lpc_order;
    compute_lpc(history->pcm, output->lpc_coeffs, &lpc_order, output->is_unvoiced);
    output->pitch_period = find_pitch_period(history->pcm, history->pitch_period);

    // 步骤3：生成候选信号（LPC合成+基音复制）
    int16_t lpc_synth[FRAME_SIZE] = { 0 };  // LPC合成信号
    int16_t pitch_copy[FRAME_SIZE] = { 0 }; // 基音复制信号

    // LPC合成（用历史帧预测）
    for (int i = 0; i < FRAME_SIZE; i++) {
        float pred = 0.0f;
        for (int k = 1; k <= lpc_order; k++) {
            pred += output->lpc_coeffs[k] * (i >= k ? history->pcm[i - k] : 0);
        }
        lpc_synth[i] = (int16_t)pred;
    }

    // 基音复制（周期扩展历史帧）
    for (int i = 0; i < FRAME_SIZE; i++) {
        int pos = (i - output->pitch_period + FRAME_SIZE) % FRAME_SIZE;
        pitch_copy[i] = history->pcm[pos];
    }

    // 步骤4：自适应融合（清浊音加权不同）
    float lpc_weight = output->is_unvoiced ? 0.7f : 0.3f;  // 清音更依赖LPC
    lpc_weight = clampf(lpc_weight - 0.1f * loss_count, 0.2f, 0.8f);  // 连续丢包调整

    for (int i = 0; i < FRAME_SIZE; i++) {
        output->pcm[i] = (int16_t)(lpc_synth[i] * lpc_weight + pitch_copy[i] * (1 - lpc_weight));
    }

    // 步骤5：帧间交叉淡化（减少突变）
    for (int i = 0; i < CROSSFADE_LEN; i++) {
        float alpha = (float)i / CROSSFADE_LEN;
        int history_pos = FRAME_SIZE - CROSSFADE_LEN + i;
        output->pcm[i] = (int16_t)(output->pcm[i] * alpha + history->pcm[history_pos] * (1 - alpha));
    }

#if 0
    // 步骤6：噪声处理（清音加匹配噪声，浊音整形）
    FFTHandler* fft = fft_init(FRAME_SIZE);
    if (output->is_unvoiced) {
        add_comfort_noise(output->pcm, history);  // 清音加匹配噪声
    }
    else {
        noise_shaping(output->pcm, fft, history);  // 浊音噪声整形
    }
    fft_cleanup(fft);
#endif
}


// 7. PLC入口函数
void plc_process(AudioFrame* output, const AudioFrame* history, bool is_lost, int loss_count) {
    if (!is_lost) {
        // 非丢包：直接复制并更新属性
        memcpy(output, history, sizeof(AudioFrame));
        output->energy = frame_energy(history->pcm);
        float dummy;
        output->is_unvoiced = is_unvoiced(history->pcm, &dummy);
        output->pitch_period = find_pitch_period(history->pcm, history->pitch_period);
    }
    else {
        // 丢包：调用隐藏逻辑
        conceal_lost_frame(output, history, loss_count);
    }
}

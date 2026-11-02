#pragma once

// OLED 를 초기화한다. 화면이 없으면 시리얼 로그로만 간다.
// setup 에서 display_node 를 찾은 뒤에 부른다.
void displayBegin();

// 표시 시간이 다 됐으면 화면을 비운다. loop 에서 매번 부른다.
void displayTick();

// 알림을 표시한다.
void displayShow(const char* title, const char* text, const char* priority);

// 표시를 지운다.
void displayClear();

// 지금 표시 중인 문구. 없으면 "". device state 의 display 필드에 그대로 싣는다.
const char* displayCurrent();
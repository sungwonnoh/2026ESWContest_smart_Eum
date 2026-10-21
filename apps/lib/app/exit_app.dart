import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// 앱을 닫고 디스플레이 홈(런처)으로 나간다.
///
/// SystemNavigator.pop 을 받아 앱을 닫는 것은 임베더(ATLAS) 몫이다 — RPi5 에서 확인한다.
/// 실수로 눌러 앱이 닫히지 않도록 한 번 묻는다.
Future<void> confirmExitApp(BuildContext context) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('홈 화면으로 나갈까요?'),
      content: const Text('HESTIA 앱을 닫습니다. 집 안 판단과 알림은 계속 동작합니다.'),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('취소'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('나가기'),
        ),
      ],
    ),
  );
  if (ok == true) await SystemNavigator.pop();
}

/// 탭 밖 화면(설정 하위, 복약 관리, 알림 상세 등)의 AppBar 에 둔다.
class ExitAppButton extends StatelessWidget {
  const ExitAppButton({super.key});

  @override
  Widget build(BuildContext context) => IconButton(
        icon: const Icon(Icons.exit_to_app_rounded),
        iconSize: 32,
        tooltip: '홈 화면으로 나가기',
        onPressed: () => confirmExitApp(context),
      );
}

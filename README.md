# Jetson Fleet AI Application Deployment Project (FL)

[![Build and Push Docker Image](https://github.com/KwakHomin/FL/actions/workflows/docker-image.yml/badge.svg)](https://github.com/KwakHomin/FL/actions/workflows/docker-image.yml)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![NVIDIA](https://img.shields.io/badge/NVIDIA-76B900?style=for-the-badge&logo=nvidia&logoColor=white)
![Ansible](https://img.shields.io/badge/Ansible-EE0000?style=for-the-badge&logo=ansible&logoColor=white)

NVIDIA Jetson 장비 여러 대에 AI 비전 애플리케이션을 배포하고, 중앙에서 원격으로 업데이트를 관리하는 프로젝트입니다.

## 주요 기능

* **실시간 AI 비전 처리:** 카메라 입력을 받아 TensorRT로 최적화된 AI 모델을 통해 실시간 객체 감지 등을 수행합니다.
* **하드웨어 제어:** AI 모델의 추론 결과에 따라 Jetson 보드의 GPIO 핀을 제어하여 외부 장치와 상호작용합니다.
* **Docker 기반 환경:** 모든 Jetson에서 동일하고 격리된 실행 환경을 보장하여 "내 컴퓨터에선 됐는데..." 문제를 원천적으로 방지합니다.
* **CI/CD 파이프라인:** GitHub Actions를 통해 코드를 `push`하면 Docker 이미지 빌드 및 Docker Hub 배포가 자동으로 이루어집니다.
* **중앙 집중식 원격 관리:** Ansible을 사용하여 단일 명령으로 모든 Jetson 장비의 소프트웨어와 AI 모델을 원격으로 업데이트합니다.

## 시스템 아키텍처

본 프로젝트는 인터넷에 연결된 환경을 기준으로, GitHub와 Docker Hub를 중심으로 한 CI/CD 파이프라인을 통해 운영됩니다.



1.  **개발 및 빌드:** 개발자가 코드를 GitHub에 `push`하면, GitHub Actions가 자동으로 Jetson용 Docker 이미지를 빌드하여 Docker Hub에 `push`합니다.
2.  **배포 및 실행:** 각 Jetson 보드는 부팅 시 `systemd` 서비스를 통해 Docker Hub에서 최신 이미지를 `pull`하여 컨테이너를 실행합니다.
3.  **원격 업데이트:** 관리자는 Ansible을 통해 모든 Jetson에 `git pull` 및 서비스 재시작 명령을 내려 업데이트를 배포합니다.

## 파일 구조

```
FL/
├── .github/workflows/
│   └── docker-image.yml      # GitHub Actions CI/CD 워크플로우
├── dockerfile                # 애플리케이션 실행 환경을 정의하는 Dockerfile
├── jetson_server.py          # Jetson에서 실행될 메인 애플리케이션
├── model.engine              # TensorRT 모델 (Git LFS로 관리)
└── requirements.txt          # 파이썬 의존성 목록
```

## 설치 및 최초 설정 (Target Jetson)

새로운 Jetson 장비를 ציוד(fleet)에 추가할 때 필요한 설정입니다.

1.  **필수 패키지 설치**
    ```bash
    sudo apt-get update
    sudo apt-get install git git-lfs -y
    ```

2.  **프로젝트 복제 (`clone`)**
    ```bash
    cd /home/<jetson-user>/
    git clone [https://github.com/KwakHomin/FL.git](https://github.com/KwakHomin/FL.git) my_project
    ```

3.  **자동 실행 서비스 등록 (`systemd`)**
    아래 내용으로 `/etc/systemd/system/fl_app.service` 파일을 생성합니다.

    ```ini
    [Unit]
    Description=FL Project Application Container
    Requires=docker.service
    After=docker.service

    [Service]
    Restart=always
    User=<jetson-user>

    ExecStart=/usr/bin/docker run --rm \
        --name fl-app-container \
        --device=/dev/gpiochip0 \
        -v /home/<jetson-user>/my_project:/app \
        KwakHomin/my-jetson-app:latest

    ExecStop=/usr/bin/docker stop fl-app-container

    [Install]
    WantedBy=multi-user.target
    ```
    *`<jetson-user>` 부분을 실제 Jetson의 사용자 이름으로 변경하세요.

4.  **서비스 활성화**
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable fl_app.service
    sudo systemctl start fl_app.service
    ```

## 업데이트 방법

관리자 PC에서 Ansible 플레이북을 실행하여 모든 장비를 원격으로 업데이트합니다.

```bash
ansible-playbook -i inventory.ini update_playbook.yml
```

---
*이 README는 2025년 8월 18일에 작성되었습니다.*

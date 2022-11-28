
try:
    from PyQt6.QtCore import QTimer, pyqtProperty, pyqtSignal
    from PyQt6.QtNetwork import (
        QHttpPart,
        QUdpSocket,
        QNetworkInterface,
        QAbstractSocket,
        QNetworkAddressEntry,
        QHostAddress,
        QNetworkRequest,
        QNetworkAccessManager,
        QNetworkReply
    )
    QIPv4Protocol = QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
    QNetworkAccessManagerOperations = QNetworkAccessManager.Operation
    QNetworkRequestAttributes = QNetworkRequest.Attribute
    QNetworkReplyNetworkErrors = QNetworkReply.NetworkError
except ImportError:
    from PyQt5.QtCore import QTimer, pyqtProperty, pyqtSignal
    from PyQt5.QtNetwork import (
        QHttpPart,
        QNetworkRequest,
        QNetworkAccessManager,
        QNetworkReply,
        QUdpSocket,
        QNetworkInterface,
        QAbstractSocket,
        QNetworkAddressEntry,
        QHostAddress
    )
    QNetworkAccessManagerOperations = QNetworkAccessManager
    QNetworkRequestAttributes = QNetworkRequest
    QNetworkReplyNetworkErrors = QNetworkReply
    if hasattr(QAbstractSocket, 'IPv4Protocol'):
        QIPv4Protocol = QAbstractSocket.IPv4Protocol
    else:
        QIPv4Protocol = QAbstractSocket.NetworkLayerProtocol.IPv4Protocol

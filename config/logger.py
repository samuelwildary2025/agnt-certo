"""
Sistema de Logging para o Agente de Supermercado
"""
import logging
import sys
from pathlib import Path
from pythonjsonlogger import jsonlogger


def setup_logger(name: str, log_file: str = "logs/agente.log", level: str = "INFO") -> logging.Logger:
    """
    Configura e retorna um logger com formatação JSON e saída para arquivo e console
    
    Args:
        name: Nome do logger
        log_file: Caminho do arquivo de log
        level: Nível de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Returns:
        Logger configurado
    """
    # Criar diretório de logs se não existir
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Criar logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Evitar duplicação de handlers
    if logger.handlers:
        return logger
    
    # Formato JSON para arquivo
    json_formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Formato legível para console
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Handler para arquivo (JSON)
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(json_formatter)

    # Handler para console (texto legível)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    # Handler adicional para arquivo em texto legível
    plain_file = Path(log_file).with_name("agente_plain.log")
    plain_file_handler = logging.FileHandler(str(plain_file), encoding='utf-8')
    plain_file_handler.setLevel(logging.DEBUG)
    plain_file_handler.setFormatter(console_formatter)
    
    # Adicionar handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.addHandler(plain_file_handler)
    
    return logger


# Logger principal da aplicação
app_logger = setup_logger("agente_supermercado")
